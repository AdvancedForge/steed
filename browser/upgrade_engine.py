#!/usr/bin/env python3
"""Try a newer PyQt/Qt WebEngine in a separate environment, then switch to it.

    .venv/bin/python -m browser.upgrade_engine check     # installed and newest versions
    .venv/bin/python -m browser.upgrade_engine try       # build .venv-upgrade and test it
    .venv/bin/python -m browser.upgrade_engine apply     # pin and install what passed
    .venv/bin/python -m browser.upgrade_engine rollback  # back to the versions before apply

``try`` never touches .venv: it installs the newest versions into
.venv-upgrade, then reports the Chromium version, the web feature check, the
startup smoke test and the test suite (``--quick`` runs a subset), and with
``--compare FILE`` the timing check against an earlier result. ``apply`` uses
only versions that passed a trial. A codec build (build_codecs.py) is for one
version; after applying, build again for the new one.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from importlib import metadata
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
TRIAL = PROJECT / ".venv-upgrade"
REPORT = TRIAL / "steed-trial.json"
PREVIOUS = PROJECT / ".venv" / "steed-previous-engine.json"
REQUIREMENTS = ("requirements-dev.txt", "requirements/mcp.txt", "requirements/apps.txt")
PACKAGES = ("PyQt6", "PyQt6-Qt6", "PyQt6-WebEngine", "PyQt6-WebEngine-Qt6")
QUICK_TESTS = (
    "test_browser.py",
    "test_engine_boundary.py",
    "test_engine_features.py",
    "test_site_settings.py",
    "test_reader_view.py",
    "test_lazy_restore.py",
    "test_permission_prompts.py",
    "test_page_inspection.py",
    "test_password_manager.py",
)
PROBE = """
import json, sys
sys.path.insert(0, %r)
from browser import engine_features, webengine
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
found = []
probe = engine_features.FeatureProbe()
probe.finished.connect(lambda features: (found.extend(features), app.quit()))
probe.start()
app.exec()
print(json.dumps({**webengine.versions(), "features": found}))
"""


def key(version: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


def installed() -> dict:
    return {name: metadata.version(name) for name in PACKAGES}


def newest() -> dict:
    """The newest release of each package on PyPI (no pre-releases)."""
    found = {}
    for name in PACKAGES:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{name}/json", timeout=30
        ) as r:
            releases = json.load(r)["releases"]
        stable = [
            version
            for version, files in releases.items()
            if files and re.fullmatch(r"\d+(\.\d+)*", version)
        ]
        found[name] = max(stable, key=key)
    return found


def check() -> int:
    current, latest = installed(), newest()
    for name in PACKAGES:
        mark = "  (newer available)" if key(latest[name]) > key(current[name]) else ""
        print(f"{name:22} installed {current[name]:10} newest {latest[name]}{mark}")
    if REPORT.exists():
        report = json.loads(REPORT.read_text())
        state = "passed" if report["passed"] else "failed"
        print(f"Last trial ({state}): {report['versions']}")
    return 0


def _run(command, **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(str(part) for part in command), flush=True)
    return subprocess.run(command, check=False, **kwargs)


def trial(quick: bool, compare: Path | None) -> int:
    target = newest()
    python = TRIAL / "bin" / "python"
    if not python.exists():
        _run([sys.executable, "-m", "venv", str(TRIAL)]).check_returncode()
    pip = [str(python), "-m", "pip", "install", "-q"]
    # The same extras as .venv, so tests of agents and apps can run.
    for extra in REQUIREMENTS:
        if (PROJECT / extra).exists():
            _run([*pip, "-r", str(PROJECT / extra)]).check_returncode()
    _run([*pip, *(f"{name}=={target[name]}" for name in PACKAGES)]).check_returncode()
    env = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu",
    )
    report = {"versions": target, "passed": False}
    probe = _run(
        [str(python), "-c", PROBE % str(PROJECT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    lines = [line for line in probe.stdout.splitlines() if line.startswith("{")]
    if lines:
        engine = json.loads(lines[-1])
        features = engine.pop("features")
        report["engine"] = engine
        report["features"] = {
            "supported": sum(f["supported"] for f in features),
            "total": len(features),
            "missing": [f["name"] for f in features if not f["supported"]],
        }
        print(f"Engine: {engine}")
        print(
            f"Features: {report['features']['supported']} of "
            f"{report['features']['total']} supported"
        )
    else:
        print(probe.stderr[-2000:])
    smoke = _run(
        [str(python), str(PROJECT / "tests" / "smoke_browser.py")],
        env=env,
        cwd=PROJECT,
        capture_output=True,
        text=True,
    )
    report["smoke"] = smoke.returncode == 0
    patterns = QUICK_TESTS if quick else ("test_*.py",)
    failures = []
    ran = 0
    for pattern in patterns:
        result = _run(
            [str(python), "-m", "unittest", "discover", "-s", "tests", "-p", pattern],
            env=env,
            cwd=PROJECT,
            capture_output=True,
            text=True,
        )
        ran += sum(
            int(found) for found in re.findall(r"^Ran (\d+) test", result.stderr, re.M)
        )
        failures += re.findall(r"^(?:FAIL|ERROR): (.+)$", result.stderr, re.M)
        if result.returncode != 0 and not failures:
            failures.append(f"{pattern} did not finish")
    report["tests"] = {"ran": ran, "failures": failures, "quick": quick}
    print(f"Tests: {ran} ran, {len(failures)} failed")
    for failure in failures[:20]:
        print("  ", failure)
    if compare is not None:
        timing = _run(
            [str(python), str(PROJECT / "tests" / "timing_check.py")]
            + ["--compare", str(compare)],
            cwd=PROJECT,
            capture_output=True,
            text=True,
        )
        report["timing"] = timing.stdout
        print(timing.stdout)
    report["passed"] = bool(lines) and report["smoke"] and not failures and ran > 0
    REPORT.write_text(json.dumps(report, indent=2))
    print("Trial passed." if report["passed"] else "Trial failed; .venv is unchanged.")
    return 0 if report["passed"] else 1


def _pin(versions: dict) -> None:
    """Pin the four packages in requirements.txt to exact versions."""
    path = PROJECT / "requirements.txt"
    lines = path.read_text().splitlines()
    for name, version in versions.items():
        pattern = re.compile(rf"^{re.escape(name)}\s*([=<>!~].*)?$", re.I)
        lines = [
            f"{name}=={version}" if pattern.match(line) else line for line in lines
        ]
    path.write_text("\n".join(lines) + "\n")


def apply() -> int:
    if not REPORT.exists():
        raise SystemExit("Run try first; apply only uses versions that passed.")
    report = json.loads(REPORT.read_text())
    if not report["passed"]:
        raise SystemExit("The last trial failed; .venv is unchanged.")
    PREVIOUS.write_text(json.dumps(installed()))
    _pin(report["versions"])
    _run(
        [str(PROJECT / ".venv" / "bin" / "python"), "-m", "pip", "install", "-q"]
        + ["-r", str(PROJECT / "requirements.txt")]
    ).check_returncode()
    print(f"Steed now uses {report['versions']}. Restart Steed.")
    if (PROJECT / ".venv").exists():
        print(
            "If you use the video-codec build, build it for this version: "
            ".venv/bin/python -m browser.build_codecs build, then install."
        )
    return 0


def rollback() -> int:
    if not PREVIOUS.exists():
        raise SystemExit("No earlier versions recorded.")
    versions = json.loads(PREVIOUS.read_text())
    _pin(versions)
    _run(
        [str(PROJECT / ".venv" / "bin" / "python"), "-m", "pip", "install", "-q"]
        + ["-r", str(PROJECT / "requirements.txt")]
    ).check_returncode()
    PREVIOUS.unlink()
    print(f"Back to {versions}. Restart Steed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("check", "try", "apply", "rollback"))
    parser.add_argument("--quick", action="store_true", help="run a subset of tests")
    parser.add_argument("--compare", type=Path, help="timing results to compare")
    args = parser.parse_args()
    if args.command == "check":
        return check()
    if args.command == "try":
        return trial(args.quick, args.compare)
    if args.command == "apply":
        return apply()
    return rollback()


if __name__ == "__main__":
    raise SystemExit(main())
