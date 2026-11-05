#!/usr/bin/env python3
"""Build Qt WebEngine with H.264/AAC video support and swap it into Steed's .venv.

The PyPI Qt WebEngine leaves out patent-licensed codecs (H.264, AAC), so many
videos and "GIFs" (short MP4 files) do not play. This builds the same Qt
WebEngine version from source with proprietary codecs turned on, then replaces
the engine files inside .venv, keeping the originals for ``restore``. Steed's
code does not change. The build is for use on this computer only.

    .venv/bin/python build_codecs.py check     # tools and packages still needed
    .venv/bin/python build_codecs.py build     # download, configure, compile
    .venv/bin/python build_codecs.py install   # back up and swap the engine
    .venv/bin/python build_codecs.py status    # which engine Steed is using
    .venv/bin/python build_codecs.py restore   # put the PyPI engine back
    .venv/bin/python build_codecs.py clean     # free the build's disk space

``build`` takes hours and can be run again to continue where it stopped.
Reinstalling PyQt6-WebEngine-Qt6 (pip) puts the PyPI engine back; run
``install`` again afterwards.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from importlib import metadata
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
DEFAULT_WORK = Path.home() / "src" / "steed-qtwebengine"
DOWNLOAD = "https://download.qt.io/official_releases/qt/{minor}/{version}/submodules/"
BACKUP = ".steed-pypi-engine"
MARKER = "steed-codecs.json"
# Engine files inside the wheel's Qt6 directory that the build replaces.
LIBRARIES = (
    "libQt6WebEngineCore.so.6",
    "libQt6WebEngineWidgets.so.6",
    "libQt6WebEngineQuick.so.6",
    "libQt6WebEngineQuickDelegatesQml.so.6",
)
RESOURCES = (
    "icudtl.dat",
    "qtwebengine_devtools_resources.pak",
    "qtwebengine_resources.pak",
    "qtwebengine_resources_100p.pak",
    "qtwebengine_resources_200p.pak",
    "v8_context_snapshot.bin",
)
# Ubuntu/Mint package names; pkg-config module -> package.
PACKAGES = {
    "nss": "libnss3-dev",
    "dbus-1": "libdbus-1-dev",
    "fontconfig": "libfontconfig1-dev",
    "freetype2": "libfreetype-dev",
    "x11-xcb": "libx11-xcb-dev",
    "xcomposite": "libxcomposite-dev",
    "xcursor": "libxcursor-dev",
    "xdamage": "libxdamage-dev",
    "xext": "libxext-dev",
    "xfixes": "libxfixes-dev",
    "xi": "libxi-dev",
    "xkbfile": "libxkbfile-dev",
    "xrandr": "libxrandr-dev",
    "xrender": "libxrender-dev",
    "xtst": "libxtst-dev",
    "xshmfence": "libxshmfence-dev",
    "xkbcommon": "libxkbcommon-dev",
    "xkbcommon-x11": "libxkbcommon-x11-dev",
    "xcb-dri3": "libxcb-dri3-dev",
    "libdrm": "libdrm-dev",
    "gbm": "libgbm-dev",
    "egl": "libegl-dev",
    "gl": "libgl-dev",
    "alsa": "libasound2-dev",
    "libpci": "libpci-dev",
    "krb5-gssapi": "libkrb5-dev",
    "libudev": "libudev-dev",
    "glib-2.0": "libglib2.0-dev",
    "expat": "libexpat1-dev",
    "zlib": "zlib1g-dev",
}
TOOLS = {"bison": "bison", "flex": "flex", "gperf": "gperf", "g++": "g++"}
CODEC_PROBE = """
const v = document.createElement('video'), a = document.createElement('audio');
JSON.stringify({
  h264: v.canPlayType('video/mp4; codecs="avc1.42E01E"'),
  aac: a.canPlayType('audio/mp4; codecs="mp4a.40.2"'),
  vp9: v.canPlayType('video/webm; codecs="vp9"'),
  mse_h264: !!(window.MediaSource
    && MediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E"')),
})"""


def default_jobs() -> int:
    """One job per 4 GB: six jobs ran out of memory on a 16 GB machine."""
    try:
        with open("/proc/meminfo") as stream:
            total_kb = int(stream.readline().split()[1])
    except (OSError, ValueError, IndexError):
        total_kb = 8 * 2**20
    return max(1, min(os.cpu_count() or 1, total_kb // (4 * 2**20)))


def qt_version() -> str:
    """The Qt WebEngine version the .venv uses; the build must match it."""
    return metadata.version("PyQt6-WebEngine-Qt6")


def wheel_dir() -> Path:
    import PyQt6

    return Path(PyQt6.__file__).resolve().parent / "Qt6"


def missing_requirements(work: Path) -> tuple[list[str], list[str]]:
    """Return (apt packages to install, other problems)."""
    packages = [name for tool, name in TOOLS.items() if shutil.which(tool) is None]
    for module, name in PACKAGES.items():
        found = subprocess.run(
            ["pkg-config", "--exists", module], check=False
        ).returncode
        if found != 0:
            packages.append(name)
    problems = []
    if shutil.which("node") is None:
        problems.append("Node.js 14 or newer is not on PATH (apt install nodejs)")
    if shutil.disk_usage(work if work.exists() else Path.home()).free < 60 * 2**30:
        problems.append("Less than 60 GB free disk space for the build")
    return sorted(set(packages)), problems


def _run(command, **kwargs):
    print("+", " ".join(str(part) for part in command), flush=True)
    subprocess.run(command, check=True, **kwargs)


def _environment(work: Path) -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{work / 'tools' / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    return env


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch_source(work: Path, version: str) -> Path:
    minor = ".".join(version.split(".")[:2])
    name = f"qtwebengine-everywhere-src-{version}"
    source = work / name
    if (source / "CMakeLists.txt").exists():
        return source
    archive = work / f"{name}.tar.xz"
    url = DOWNLOAD.format(minor=minor, version=version) + archive.name
    if not archive.exists():
        print(f"Downloading {url}", flush=True)
        urllib.request.urlretrieve(url, archive.with_suffix(".part"))
        archive.with_suffix(".part").rename(archive)
    with urllib.request.urlopen(url + ".sha256") as response:
        expected = response.read().decode().split()[0]
    if _digest(archive) != expected:
        archive.unlink()
        raise SystemExit("The downloaded source did not match Qt's checksum")
    print("Unpacking the source", flush=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(work, filter="data")
    return source


def _prepare_tools(work: Path, version: str) -> Path:
    """Build tools in their own venv, and Qt's matching development files."""
    tools = work / "tools"
    if not (tools / "bin" / "aqt").exists():
        _run([sys.executable, "-m", "venv", str(tools)])
        _run(
            [str(tools / "bin" / "pip"), "install", "-q"]
            + ["aqtinstall", "cmake", "ninja", "html5lib"]
        )
    qt = work / "qt" / version / "gcc_64"
    if not (qt / "bin" / "qt-configure-module").exists():
        _run(
            [str(tools / "bin" / "aqt"), "install-qt", "-O", str(work / "qt")]
            + ["linux", "desktop", version, "linux_gcc_64"]
            + ["-m", "qtwebchannel", "qtpositioning"],
        )
    return qt


def build(work: Path, jobs: int) -> None:
    version = qt_version()
    work.mkdir(parents=True, exist_ok=True)
    packages, problems = missing_requirements(work)
    if packages or problems:
        report(packages, problems)
        raise SystemExit(1)
    qt = _prepare_tools(work, version)
    source = _fetch_source(work, version)
    env = _environment(work)
    # Chromium's part is built by an inner ninja that otherwise starts one job
    # per core plus two; Qt reads this when the build is configured.
    env["NINJAFLAGS"] = f"-j{jobs}"
    out = work / "build"
    if not (out / "build.ninja").exists():
        out.mkdir(exist_ok=True)
        _run(
            [str(qt / "bin" / "qt-configure-module"), str(source)]
            + ["-webengine-proprietary-codecs", "-no-build-qtpdf"]
            + ["--", f"-DPython3_EXECUTABLE={work / 'tools' / 'bin' / 'python'}"]
            # As Qt ships it: without debug information the library is far
            # smaller and linking it fits in ordinary memory.
            + ["-DCMAKE_BUILD_TYPE=Release"],
            cwd=out,
            env=env,
        )
    else:
        # Reconfiguring keeps compiled work; it only renews the job limit.
        _run(["cmake", "."], cwd=out, env=env)
    _run(["cmake", "--build", ".", "--parallel", str(jobs)], cwd=out, env=env)
    stage = work / "stage"
    _run(["cmake", "--install", ".", "--prefix", str(stage)], cwd=out, env=env)
    core = stage / "lib" / LIBRARIES[0]
    (stage / MARKER).write_text(
        json.dumps({"version": version, "core": _digest(core.resolve())})
    )
    print(
        f"Built Qt WebEngine {version}. Run: .venv/bin/python build_codecs.py install"
    )


def _swaps(stage: Path, target: Path) -> list[tuple[Path, Path]]:
    """(built file, wheel file) pairs; every wheel file needs a built copy."""
    pairs = []
    for name in LIBRARIES:
        pairs.append((stage / "lib" / name, target / "lib" / name))
    pairs.append(
        (
            stage / "libexec" / "QtWebEngineProcess",
            target / "libexec" / "QtWebEngineProcess",
        )
    )
    for name in RESOURCES:
        pairs.append((stage / "resources" / name, target / "resources" / name))
    return [(built, wheel) for built, wheel in pairs if wheel.exists()]


def _exports(library: Path) -> set[str]:
    result = subprocess.run(
        ["nm", "-D", "--defined-only", str(library)],
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.split()[-1] for line in result.stdout.splitlines() if line.strip()}


def _imported(target: Path) -> set[str]:
    """Names that PyQt6 and the wheel's Qt libraries need from other libraries."""
    files = [*target.parent.glob("*.so"), *target.rglob("*.so*")]
    files.append(target / "libexec" / "QtWebEngineProcess")
    names = set()
    for path in files:
        if not path.is_file() or BACKUP in path.parts:
            continue
        result = subprocess.run(
            ["nm", "-D", "--undefined-only", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        names.update(
            line.split()[-1] for line in result.stdout.splitlines() if line.strip()
        )
    return names


def check_compatible(stage: Path, target: Path) -> None:
    """Refuse a build whose version or exported functions differ from the wheel."""
    marker = stage / MARKER
    if not marker.exists():
        raise SystemExit("No finished build found; run build first")
    built = json.loads(marker.read_text())["version"]
    if built != qt_version():
        raise SystemExit(
            f"The build is Qt {built} but .venv uses {qt_version()}; build again"
        )
    for built_file, wheel_file in _swaps(stage, target):
        if not built_file.exists():
            raise SystemExit(f"The build has no {built_file.name}")
        if wheel_file.parent.name == "lib":
            original = target / BACKUP / wheel_file.relative_to(target)
            reference = original if original.exists() else wheel_file
            missing = _exports(reference) - _exports(built_file)
            # A different compiler exports a few of its own internals (such as
            # libstdc++ digit tables); only names something here imports matter.
            used = missing & _imported(target)
            if used:
                raise SystemExit(
                    f"{built_file.name} lacks {len(used)} functions PyQt6 or Qt "
                    f"uses, e.g. {sorted(used)[0]}"
                )
            if missing:
                print(
                    f"{built_file.name}: {len(missing)} exported names differ, "
                    "none of them used here."
                )


def install(stage: Path, target: Path) -> None:
    check_compatible(stage, target)
    backup = target / BACKUP
    for built, wheel in _swaps(stage, target):
        saved = backup / wheel.relative_to(target)
        # Keep the PyPI file; pip may have put it back since the last install.
        if _digest(wheel) != _digest(built.resolve()):
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(wheel, saved)
        temporary = wheel.with_name(wheel.name + ".new")
        shutil.copy2(built.resolve(), temporary)
        os.replace(temporary, wheel)
    marker = json.loads((stage / MARKER).read_text())
    marker["installed"] = _stamp(target / "lib" / LIBRARIES[0])
    (target / MARKER).write_text(json.dumps(marker))
    print("Installed the engine with video codecs. Restart Steed to use it.")
    print("To free the build's disk space (keeping this engine): build_codecs.py clean")


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(
        p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink()
    )


def clean(work: Path, assume_yes: bool = False) -> None:
    """Remove everything but the finished engine (stage), which install uses."""
    if not (work / "stage" / MARKER).exists():
        raise SystemExit("No finished build to keep; nothing removed.")
    # Only what build creates: never anything else that shares the folder.
    names = ("build", "qt", "tools", "aqt.log", "aqtinstall.log", "download.log")
    targets = [
        path
        for path in sorted(work.iterdir())
        if path.name in names or path.name.startswith("qtwebengine-everywhere-src-")
    ]
    total = sum(_size(path) for path in targets)
    print(f"Removes {total / 2**30:.1f} GB from {work}:")
    for path in targets:
        print("  ", path.name)
    print("Keeps stage/ (the built engine, for install) and build.log.")
    print("A later build downloads and compiles everything again (hours).")
    if not assume_yes and input("Remove them? [y/N] ").strip().lower() != "y":
        print("Nothing removed.")
        return
    for path in targets:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    print("Removed.")


def restore(target: Path) -> None:
    backup = target / BACKUP
    if not backup.exists():
        print("Steed is already using the PyPI engine.")
        return
    for saved in sorted(path for path in backup.rglob("*") if path.is_file()):
        wheel = target / saved.relative_to(backup)
        temporary = wheel.with_name(wheel.name + ".new")
        shutil.copy2(saved, temporary)
        os.replace(temporary, wheel)
    (target / MARKER).unlink(missing_ok=True)
    shutil.rmtree(backup)
    print("Restored the PyPI engine. Restart Steed to use it.")


def probe_codecs() -> dict:
    """Ask the engine in .venv which formats it plays."""
    script = (
        "import sys\n"
        "from PyQt6.QtCore import QTimer, QUrl\n"
        "from PyQt6.QtWidgets import QApplication\n"
        "from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile\n"
        "app = QApplication(sys.argv)\n"
        "profile = QWebEngineProfile()\n"
        "page = QWebEnginePage(profile)\n"
        f"code = {CODEC_PROBE!r}\n"
        "page.loadFinished.connect(lambda ok: page.runJavaScript("
        "code, lambda r: (print(r), app.quit())))\n"
        "page.setHtml('<p>', QUrl('https://localhost/'))\n"
        "QTimer.singleShot(30000, app.quit)\n"
        "app.exec()\n"
        "del page\n"
    )
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    for line in result.stdout.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    return {}


def _stamp(path: Path) -> list[int]:
    info = path.stat()
    return [info.st_size, info.st_mtime_ns]


def using_build(target: Path, *, thorough: bool = True) -> bool:
    """True when the wheel's engine is the codec build (pip may undo it).

    thorough=False compares only size and modification time, which pip always
    changes when it writes its own file; it is cheap enough for every startup.
    """
    marker = target / MARKER
    core = target / "lib" / LIBRARIES[0]
    try:
        recorded = json.loads(marker.read_text())
        if not thorough:
            return recorded.get("installed") == _stamp(core)
        return recorded.get("core") == _digest(core)
    except (OSError, ValueError):
        return False


def replaced_by_pip(target: Path) -> bool:
    """The codec build was installed here, and the PyPI engine is back."""
    return (target / BACKUP).is_dir() and not using_build(target, thorough=False)


def status(target: Path) -> None:
    built = using_build(target)
    engine = "built with video codecs" if built else "PyPI (no H.264/AAC)"
    print(f"Qt WebEngine {qt_version()}: {engine}")
    if not built and (target / BACKUP).exists():
        print("  pip replaced the codec build; run install again")
    support = probe_codecs()
    for name, label in (("h264", "H.264"), ("aac", "AAC"), ("vp9", "VP9")):
        print(f"  {label}: {'plays' if support.get(name) else 'not supported'}")


def report(packages: list[str], problems: list[str]) -> None:
    if packages:
        print("Install the build packages first:")
        print(f"  sudo apt install {' '.join(packages)}")
    for problem in problems:
        print(problem)
    if not packages and not problems:
        print("Everything needed for the build is installed.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", choices=("check", "build", "install", "status", "restore", "clean")
    )
    parser.add_argument("--yes", action="store_true", help="clean without asking")
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    parser.add_argument(
        "--jobs",
        type=int,
        default=default_jobs(),
        help="parallel compile jobs (default: one per 4 GB of memory); "
        "Chromium's largest files need about that much each",
    )
    args = parser.parse_args()
    target = wheel_dir()
    if args.command == "check":
        report(*missing_requirements(args.work_dir))
    elif args.command == "build":
        build(args.work_dir, args.jobs)
    elif args.command == "install":
        install(args.work_dir / "stage", target)
    elif args.command == "status":
        status(target)
    elif args.command == "clean":
        clean(args.work_dir, args.yes)
    else:
        restore(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
