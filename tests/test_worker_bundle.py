"""Locked wheels, hostile archives, offline installation and real isolated imports."""

import hashlib
import io
import stat
import tempfile
import threading
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from browser import install_worker_bundle
from services import worker_bundle as bundle
from services.agent_tools import TOOLS_BY_NAME
from services.local_worker import run_worker
from services.worker_diagnostics import DOCUMENT_PROBE


def synthetic_wheel(
    name="example.py", data=b"raise RuntimeError('never host import')", mode=None
):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        item = zipfile.ZipInfo(name)
        if mode is not None:
            item.external_attr = mode << 16
        archive.writestr(item, data)
    raw = output.getvalue()
    return raw, {
        "filename": "synthetic.whl",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


class WorkerBundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.addCleanup(self.directory.cleanup)

    def test_published_runtime_argument_accepts_only_the_maintained_environments(self):
        tool = TOOLS_BY_NAME["steed_worker_run"]
        args = {"extension_id": "package", "request_id": "run"}
        for runtime in ("stdlib", "documents"):
            tool.validate({**args, "runtime": runtime})
        for runtime in ("custom", "/tmp/env", {}, None):
            with self.subTest(runtime=runtime), self.assertRaises(ValueError):
                tool.validate({**args, "runtime": runtime})

    def test_missing_optional_bundle_preserves_standard_environment(self):
        with patch.object(bundle, "BUNDLE_ROOT", self.root):
            bundle.require_installed(bundle.environment())
            with self.assertRaisesRegex(bundle.BundleError, "not_installed"):
                bundle.require_installed(bundle.environment("documents"))
        for runtime in ("arbitrary", "/tmp/dependencies", [], None):
            with self.subTest(runtime=runtime), self.assertRaises(bundle.BundleError):
                bundle.environment(runtime)

    def test_environment_identity_includes_trusted_runtime_and_entire_lock(self):
        first = bundle.environment("documents")
        self.assertNotEqual(first.sha256, bundle.environment().sha256)
        lock = self.root / "lock.json"
        lock.write_bytes(bundle.LOCK_PATH.read_bytes() + b"\n")
        with patch.object(bundle, "LOCK_PATH", lock):
            self.assertNotEqual(first.sha256, bundle.environment("documents").sha256)

    def test_unsupported_platform_fails_without_resolver_or_fallback(self):
        for machine, libc in (
            ("riscv64", ("glibc", "2.39")),
            ("x86_64", ("musl", "1.2")),
            ("x86_64", ("glibc", "2.26")),
        ):
            with (
                patch.object(bundle.platform, "machine", return_value=machine),
                patch.object(bundle.platform, "libc_ver", return_value=libc),
            ):
                with self.assertRaisesRegex(bundle.BundleError, "platform_unsupported"):
                    bundle.environment("documents")

    def test_tampered_wheel_bytes_are_rejected_even_at_the_same_size(self):
        raw, wheel = synthetic_wheel()
        target = self.root / wheel["filename"]
        target.write_bytes(raw)
        self.assertEqual(bundle.wheel_bytes(self.root, wheel), raw)
        target.write_bytes(b"x" + raw[1:])
        with self.assertRaisesRegex(bundle.BundleError, "invalid"):
            bundle.wheel_bytes(self.root, wheel)

    def test_wheel_symlink_is_rejected(self):
        raw, wheel = synthetic_wheel()
        target = self.root / "actual"
        target.write_bytes(raw)
        (self.root / wheel["filename"]).symlink_to(target)
        with self.assertRaises(bundle.BundleError):
            bundle.wheel_bytes(self.root, wheel)

    def test_archive_traversal_symlinks_devices_and_data_hooks_are_rejected(self):
        for name, mode in (
            ("../escape.py", None),
            ("/escape.py", None),
            ("a/../../escape", None),
            ("a\\escape", None),
            ("a//escape", None),
            ("link", stat.S_IFLNK | 0o777),
            ("device", stat.S_IFCHR | 0o600),
            ("x.data/scripts/run", None),
        ):
            with self.subTest(name=name, mode=mode):
                raw, _ = synthetic_wheel(name, mode=mode)
                with self.assertRaises(bundle.BundleError):
                    bundle.extract_wheel(raw, self.root, set())
        self.assertFalse((self.root.parent / "escape.py").exists())

    def test_cross_wheel_collisions_and_expansion_limits_are_rejected(self):
        raw, _ = synthetic_wheel()
        occupied = set()
        bundle.extract_wheel(raw, self.root, occupied)
        with self.assertRaises(bundle.BundleError):
            bundle.extract_wheel(raw, self.root, occupied)
        with (
            patch.object(bundle, "MAX_EXPANDED", 1),
            self.assertRaises(bundle.BundleError),
        ):
            bundle.extract_wheel(raw, self.root, set())
        with (
            patch.object(bundle, "MAX_FILES", 0),
            self.assertRaises(bundle.BundleError),
        ):
            bundle.extract_wheel(raw, self.root, set())

    def test_offline_install_is_atomic_idempotent_and_never_imports_or_runs_pip(self):
        raw, wheel = synthetic_wheel(
            "attack.pth", b"import os; raise RuntimeError('host execution')"
        )
        offline = self.root / "offline"
        offline.mkdir()
        (offline / wheel["filename"]).write_bytes(raw)
        env = replace(bundle.environment("documents"), wheels=(wheel,))
        with (
            patch.object(bundle, "BUNDLE_ROOT", self.root / "bundles"),
            patch.object(bundle, "environment", return_value=env),
            patch.object(install_worker_bundle.subprocess, "run") as execute,
        ):
            self.assertEqual(
                install_worker_bundle.install(wheel_directory=offline), env
            )
            self.assertEqual(
                install_worker_bundle.install(wheel_directory=offline), env
            )
            execute.assert_not_called()
            self.assertEqual(
                list(bundle.BUNDLE_ROOT.iterdir()), [bundle.bundle_directory(env)]
            )
            target = bundle.bundle_directory(env)
            target.chmod(0o700)  # Temporary fixture cleanup only.

    def test_bad_offline_install_publishes_no_partial_environment(self):
        raw, wheel = synthetic_wheel("../escape", b"unsafe")
        (self.root / wheel["filename"]).write_bytes(raw)
        env = replace(bundle.environment("documents"), wheels=(wheel,))
        with (
            patch.object(bundle, "BUNDLE_ROOT", self.root / "bundles"),
            patch.object(bundle, "environment", return_value=env),
        ):
            with self.assertRaises(bundle.BundleError):
                install_worker_bundle.install(wheel_directory=self.root)
            self.assertFalse(bundle.bundle_directory(env).exists())
            self.assertEqual(list(bundle.BUNDLE_ROOT.iterdir()), [])

    def test_changed_environment_is_rejected_before_launch(self):
        env = replace(bundle.environment(), sha256="0" * 64)
        with patch("services.local_worker.subprocess.Popen") as launch:
            outcome = run_worker(
                {"main.py": b"pass"},
                "main.py",
                threading.Event(),
                lambda message: {},
                environment=env,
            )
        self.assertEqual(outcome.reason, "worker_environment_changed")
        launch.assert_not_called()

    def test_changed_trusted_helper_snapshot_cannot_use_previous_environment_approval(
        self,
    ):
        env = bundle.environment()
        sources = bundle.runtime_sources()
        sources["worker_sdk.py"] += b"\n# changed helper\n"
        with (
            patch("services.local_worker.runtime_sources", return_value=sources),
            patch("services.local_worker.subprocess.Popen") as launch,
        ):
            result = run_worker(
                {"main.py": b"pass"},
                "main.py",
                threading.Event(),
                lambda message: {},
                environment=env,
            )
        self.assertEqual(result.reason, "worker_environment_changed")
        launch.assert_not_called()

    def test_real_bundle_generators_and_native_image_import_remain_isolated(self):
        env = bundle.environment("documents")
        try:
            bundle.require_installed(env)
        except bundle.BundleError:
            self.skipTest(
                "Install the optional document bundle for native-library acceptance"
            )
        result = run_worker(
            {"check.py": DOCUMENT_PROBE},
            "check.py",
            threading.Event(),
            lambda message: self.fail("Unexpected probe RPC"),
            environment=env,
            timeout=10,
        )
        self.assertEqual(result.state, "succeeded", result)

    def test_inert_pth_and_readonly_dependencies_have_no_host_or_process_authority(
        self,
    ):
        raw, wheel = synthetic_wheel(
            "attack.pth", b"raise RuntimeError('pth executed')"
        )
        env = replace(bundle.environment("documents"), wheels=(wheel,))
        root = self.root / env.name
        root.mkdir()
        (root / wheel["filename"]).write_bytes(raw)
        with patch.object(bundle, "BUNDLE_ROOT", self.root):
            result = run_worker(
                {
                    "main.py": b"""
import errno, os, socket, subprocess, fcntl, termios
from pathlib import Path
from tempfile import NamedTemporaryFile
assert not Path('/home').exists() and not Path('/run/user').exists()
with NamedTemporaryFile(mode='w+') as scratch:
    fcntl.ioctl(scratch.fileno(), termios.FIOCLEX)
    for attempt in [lambda: fcntl.ioctl(scratch.fileno(), termios.FIONBIO, b'\\0'*4), socket.socket, os.fork, lambda: subprocess.run(['/usr/bin/true']), lambda: Path('/dependencies/attack.pth').write_bytes(b'changed')]:
        try:
            attempt()
        except OSError:
            pass
        else:
            raise AssertionError('forbidden operation worked')
assert not Path('/usr/lib/python3/dist-packages/PyQt6').exists() or 'PyQt6' not in __import__('sys').modules
"""
                },
                "main.py",
                threading.Event(),
                lambda message: {},
                environment=env,
                timeout=5,
            )
        self.assertEqual(result.state, "succeeded", result)
