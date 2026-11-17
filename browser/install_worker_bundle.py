#!/usr/bin/env python3
"""Download an optional maintained wheel set; never install into host Python."""

import argparse
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from services import worker_bundle


def install(*, wheel_directory=None):
    env = worker_bundle.environment("documents")
    root = worker_bundle.BUNDLE_ROOT
    if root.is_symlink():
        raise worker_bundle.BundleError("worker_bundle_invalid")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = worker_bundle.bundle_directory(env)
    if target.is_symlink():
        raise worker_bundle.BundleError("worker_bundle_invalid")
    if target.exists():
        for wheel in env.wheels:
            worker_bundle.wheel_bytes(target, wheel)
        return env
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as directory:
        staging = Path(directory)
        wheels = staging / env.name
        wheels.mkdir(mode=0o700)
        if wheel_directory is None:
            requirements = staging / "requirements.txt"
            requirements.write_text(
                "\n".join(f"{w['url']} --hash=sha256:{w['sha256']}" for w in env.wheels)
                + "\n"
            )
            print(
                "Downloading pinned document wheels (no source builds or host installation)…",
                flush=True,
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "--isolated",
                    "--disable-pip-version-check",
                    "download",
                    "--no-cache-dir",
                    "--no-deps",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "--dest",
                    str(wheels),
                    "-r",
                    str(requirements),
                ],
                check=True,
                timeout=120,
            )
        else:
            for wheel in env.wheels:
                (wheels / wheel["filename"]).write_bytes(
                    worker_bundle.wheel_bytes(wheel_directory, wheel)
                )
        occupied, expanded = set(), 0
        unpacked = staging / "validation"
        unpacked.mkdir()
        for wheel in env.wheels:
            data = worker_bundle.wheel_bytes(wheels, wheel)
            expanded = worker_bundle.extract_wheel(
                data, unpacked, occupied, expanded=expanded
            )
            (wheels / wheel["filename"]).chmod(0o400)
        # Atomic publication; an existing version is never overwritten.
        os.rename(wheels, target)
        target.chmod(0o500)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel-directory",
        type=Path,
        help="Use an offline directory of exact locked wheels",
    )
    parser.add_argument(
        "--destination", type=Path, help="Override bundle root for testing or packaging"
    )
    args = parser.parse_args()
    if args.destination:
        worker_bundle.BUNDLE_ROOT = args.destination.resolve()
    try:
        env = install(wheel_directory=args.wheel_directory)
        from services.local_worker import run_worker
        from services.worker_diagnostics import DOCUMENT_PROBE

        outcome = run_worker(
            {"check.py": DOCUMENT_PROBE},
            "check.py",
            threading.Event(),
            lambda message: (_ for _ in ()).throw(RuntimeError("Unexpected setup RPC")),
            environment=env,
            timeout=10,
        )
        if outcome.state != "succeeded":
            raise worker_bundle.BundleError("worker_bundle_sandbox_check_failed")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(
            f"Bundle setup failed: {type(error).__name__}. Check wheel access and sandbox setup; no host fallback.",
            file=sys.stderr,
        )
        return 1
    print(
        f"{env.name} ready: PDF, XLSX and PNG generation verified inside the sandbox."
    )
    print("Restart Steed or use Task Permissions → Workers → Check Worker Setup.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
