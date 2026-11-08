#!/usr/bin/env python3
"""A translation service on this computer, for View → Translate Page (optional).

    .venv/bin/python setup_translation.py install [--languages en,de,fr]
    .venv/bin/python setup_translation.py start
    .venv/bin/python setup_translation.py status
    .venv/bin/python setup_translation.py stop
    .venv/bin/python setup_translation.py uninstall

install puts LibreTranslate (open source, offline translation) in its own
Python environment under ~/src/steed-libretranslate, with CPU-only packages,
and sets Steed's translation service to it. start runs it at
http://127.0.0.1:5000 (this computer only); its language models download the
first time (about 100 MB per language). Pages then never leave the computer
for translation. uninstall removes the environment and its models.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
DEFAULT_HOME = Path.home() / "src" / "steed-libretranslate"
PORT = 5000
SERVICE = f"http://127.0.0.1:{PORT}/translate"
DEFAULT_LANGUAGES = "en,de,fr,es,it,pt,nl,pl,ru,uk,ja,zh,ko,ar"
CPU_WHEELS = "https://download.pytorch.org/whl/cpu"


def paths(home: Path) -> dict:
    return {
        "venv": home / "venv",
        "python": home / "venv" / "bin" / "python",
        "server": home / "venv" / "bin" / "libretranslate",
        "pid": home / "libretranslate.pid",
        "log": home / "libretranslate.log",
        "data": home / "data",
        "settings": home / "languages.txt",
    }


def running(home: Path) -> int | None:
    """The server's process ID while it runs."""
    try:
        pid = int(paths(home)["pid"].read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


def answering(timeout=2.0) -> list | None:
    """The languages the service offers, or None when it does not answer."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/languages", timeout=timeout
        ) as reply:
            return json.load(reply)
    except (OSError, ValueError):
        return None


def set_steed_service(value: str) -> None:
    """Point Steed's translation setting here (or clear it)."""
    sys.path.insert(0, str(PROJECT))
    from config import Config

    Config().set("translate_service", value)


def install(home: Path, languages: str) -> int:
    p = paths(home)
    home.mkdir(parents=True, exist_ok=True)
    if not p["python"].exists():
        subprocess.run([sys.executable, "-m", "venv", str(p["venv"])], check=True)
    pip = [str(p["python"]), "-m", "pip", "install", "--upgrade"]
    subprocess.run([*pip, "pip"], check=True)
    # CPU-only PyTorch is a fraction of the default download.
    subprocess.run(
        [*pip, "--extra-index-url", CPU_WHEELS, "libretranslate"], check=True
    )
    p["settings"].write_text(languages)
    set_steed_service(SERVICE)
    print(f"Installed. Steed now translates through {SERVICE}.")
    print("Run: .venv/bin/python setup_translation.py start")
    return 0


def start(home: Path) -> int:
    p = paths(home)
    if not p["server"].exists():
        print("Not installed. Run: .venv/bin/python setup_translation.py install")
        return 1
    if running(home):
        print("Already running.")
        return 0
    languages = (
        p["settings"].read_text().strip()
        if p["settings"].exists()
        else DEFAULT_LANGUAGES
    )
    env = dict(os.environ, LT_LOAD_ONLY=languages, XDG_DATA_HOME=str(p["data"]))
    with p["log"].open("ab") as log:
        process = subprocess.Popen(
            [
                str(p["server"]),
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
                "--load-only",
                languages,
                "--disable-web-ui",
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    p["pid"].write_text(str(process.pid))
    print(f"Starting (models download the first time; log: {p['log']})…")
    end = time.monotonic() + 600
    while time.monotonic() < end:
        if process.poll() is not None:
            print("It stopped while starting; see the log.")
            return 1
        if answering():
            print(f"Running at {SERVICE}.")
            return 0
        time.sleep(2)
    print("Still starting; check status in a minute.")
    return 0


def stop(home: Path) -> int:
    pid = running(home)
    if pid is None:
        print("Not running.")
        return 0
    os.killpg(pid, signal.SIGTERM)
    paths(home)["pid"].unlink(missing_ok=True)
    print("Stopped.")
    return 0


def status(home: Path) -> int:
    p = paths(home)
    if not p["server"].exists():
        print("Not installed.")
        return 1
    languages = answering()
    if languages is None:
        print("Installed, not running." if not running(home) else "Starting…")
        return 1
    codes = ", ".join(sorted(language["code"] for language in languages))
    print(f"Running at {SERVICE} with: {codes}")
    return 0


def uninstall(home: Path) -> int:
    stop(home)
    if home.exists():
        shutil.rmtree(home)
    set_steed_service("")
    print("Removed. Steed's translation service setting is cleared.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", choices=("install", "start", "stop", "status", "uninstall")
    )
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument(
        "--languages",
        default=DEFAULT_LANGUAGES,
        help="language codes to load (each about 100 MB)",
    )
    args = parser.parse_args()
    commands = {
        "install": lambda: install(args.home, args.languages),
        "start": lambda: start(args.home),
        "stop": lambda: stop(args.home),
        "status": lambda: status(args.home),
        "uninstall": lambda: uninstall(args.home),
    }
    return commands[args.command]()


if __name__ == "__main__":
    raise SystemExit(main())
