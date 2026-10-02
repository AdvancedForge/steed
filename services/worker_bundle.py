"""Maintained wheel bytes, never host-imported or chosen by generated code."""

import hashlib
import io
import json
import platform
import stat
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REPOSITORY = Path(__file__).resolve().parent.parent
BUNDLE_ROOT = REPOSITORY / ".worker-bundles"
LOCK_PATH = REPOSITORY / "worker_bundle.lock.json"
MAX_EXPANDED = 96 * 1024 * 1024
MAX_FILES = 5000


class BundleError(ValueError):
    """An optional environment is unavailable; never a task-journal failure."""


@dataclass(frozen=True)
class WorkerEnvironment:
    runtime: str
    name: str
    sha256: str
    libraries: tuple
    wheels: tuple

    def metadata(self):
        return {
            "runtime": self.runtime,
            "name": self.name,
            "environment_sha256": self.sha256,
            "libraries": dict(self.libraries),
        }


def runtime_sources():
    """Capture the trusted code bytes used to identify and start one environment."""
    source = Path(__file__).parent
    return {
        name: (source / name).read_bytes()
        for name in (
            "worker_bootstrap.py",
            "worker_sdk.py",
            "local_worker.py",
            "worker_bundle.py",
        )
    }


def environment(runtime="stdlib", *, trusted_sources=None):
    """Small trusted metadata only on Qt thread; wheel I/O belongs to workers."""
    if not isinstance(runtime, str) or runtime not in {"stdlib", "documents"}:
        raise BundleError("worker_runtime_unknown")
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            [runtime, sys.version, platform.machine(), str(sys._base_executable)]
        ).encode()
    )
    for data in (
        runtime_sources() if trusted_sources is None else trusted_sources
    ).values():
        digest.update(data)
    if runtime == "stdlib":
        return WorkerEnvironment(
            runtime, "Python standard library", digest.hexdigest(), (), ()
        )
    lock_bytes = LOCK_PATH.read_bytes()
    lock = json.loads(lock_bytes)
    digest.update(lock_bytes)
    # These are the maintained binary wheels, not a general dependency resolver.
    machine = platform.machine()
    libc, version = platform.libc_ver()
    if (
        sys.implementation.name != "cpython"
        or machine not in {"x86_64", "aarch64"}
        or libc != "glibc"
        or tuple(int(v) for v in version.split(".")[:2]) < (2, 28)
    ):
        raise BundleError("worker_bundle_platform_unsupported")
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    selected = []
    for package in lock["packages"]:
        candidates = [
            w
            for w in package["wheels"]
            if w["filename"].endswith("none-any.whl")
            or f"-{tag}-{tag}-" in w["filename"]
            and w["filename"].endswith(f"_{machine}.whl")
        ]
        if len(candidates) != 1:
            raise BundleError("worker_bundle_platform_unsupported")
        selected.append(candidates[0])
        digest.update(candidates[0]["sha256"].encode())
    return WorkerEnvironment(
        runtime,
        lock["name"],
        digest.hexdigest(),
        tuple((p["name"], p["version"]) for p in lock["packages"]),
        tuple(selected),
    )


def bundle_directory(env):
    return BUNDLE_ROOT / env.name


def require_installed(env):
    if not env.wheels:
        return
    try:
        root = bundle_directory(env)
        if root.is_symlink() or BUNDLE_ROOT.is_symlink():
            raise BundleError("worker_bundle_invalid")
        for wheel in env.wheels:
            path = root / wheel["filename"]
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != wheel["size"]
            ):
                raise BundleError("worker_bundle_not_installed")
    except OSError:
        raise BundleError("worker_bundle_not_installed") from None


def wheel_bytes(directory, wheel):
    """Snapshot each wheel once; ZIP extraction uses the verified in-memory bytes."""
    path = directory / wheel["filename"]
    if path.is_symlink() or not path.is_file() or path.stat().st_size != wheel["size"]:
        raise BundleError("worker_bundle_invalid")
    with path.open("rb") as stream:
        data = stream.read(wheel["size"] + 1)
    if (
        len(data) != wheel["size"]
        or hashlib.sha256(data).hexdigest() != wheel["sha256"]
    ):
        raise BundleError("worker_bundle_invalid")
    return data


def extract_wheel(data, destination, occupied, *, expanded=0):
    """No setup hooks, .pth evaluation, executables, traversal or symlinks."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in info.filename
                or "\x00" in info.filename
                or not name.parts
                or str(name) != info.filename.rstrip("/")
                or stat.S_ISLNK(mode)
                or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                or any(part.endswith(".data") for part in name.parts)
            ):
                raise BundleError("worker_bundle_unsafe_wheel")
            if info.is_dir():
                continue
            if str(name) in occupied or len(occupied) >= MAX_FILES:
                raise BundleError("worker_bundle_unsafe_wheel")
            expanded += info.file_size
            if expanded > MAX_EXPANDED:
                raise BundleError("worker_bundle_size_limit")
            occupied.add(str(name))
            target = destination.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            # .pth files stay inert: Python runs -S with explicit dependency path.
            target.write_bytes(archive.read(info))
            target.chmod(0o400)
    return expanded


def materialize(env, destination, cancelled):
    """Verify and copy only maintained wheels, off Qt thread and before execution."""
    require_installed(env)
    destination.mkdir(mode=0o700)
    occupied, expanded = set(), 0
    for wheel in env.wheels:
        if cancelled.is_set():
            raise BundleError("worker_environment_cancelled")
        data = wheel_bytes(bundle_directory(env), wheel)
        expanded = extract_wheel(data, destination, occupied, expanded=expanded)
