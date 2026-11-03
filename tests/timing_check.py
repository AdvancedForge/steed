"""Time Steed's everyday actions and measure its memory, in a temporary profile.

    .venv/bin/python tests/timing_check.py                  # 3 rounds, median
    .venv/bin/python tests/timing_check.py --save before.json
    .venv/bin/python tests/timing_check.py --compare before.json

Each round starts Steed fresh through main.main() and uses only local pages,
so numbers compare runs on this computer, not computers with each other. Close
other heavy programs for steadier figures. The window opens on the current
display; pass --offscreen to run without one (no GPU, so different numbers).
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TABS = 10
# name, label, unit; lower is better for every figure.
METRICS = (
    ("startup", "Start to window ready", "ms"),
    ("first_page", "Start to first page loaded", "ms"),
    ("new_tab", "Open a tab and load a page", "ms"),
    ("tab_switch", "Switch tabs", "ms"),
    ("typing", "Type 20 characters in the address bar", "ms"),
    ("memory_one", "Memory, one tab", "MB"),
    ("memory_ten", f"Memory, {TABS} tabs", "MB"),
    ("shutdown", "Close the window and quit", "ms"),
)
PAGE = """<!doctype html><meta charset=utf-8><title>Page {n}</title>
<style>li{{padding:2px;border-bottom:1px solid #ddd}}</style>
<h1>Page {n}</h1><ul>{items}</ul>
<script>for (let i = 0; i < 200; i++) document.querySelector('ul')
  .insertAdjacentHTML('beforeend', '<li>script item ' + i + '</li>');</script>"""


def seconds_since_process_start() -> float:
    """Includes Python's own start-up, which time.monotonic() alone misses."""
    with open("/proc/self/stat") as stream:
        started = int(stream.read().rsplit(")", 1)[1].split()[19])
    with open("/proc/uptime") as stream:
        uptime = float(stream.read().split()[0])
    return uptime - started / os.sysconf("SC_CLK_TCK")


def run_round(offscreen: bool) -> dict:
    """One fresh Steed session; prints its figures as JSON."""
    process_start = time.monotonic() - seconds_since_process_start()
    sys.path.insert(0, str(ROOT))
    from PyQt6.QtCore import QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    import main
    import memory_modes
    from browser_window import BrowserWindow
    from config import Config

    results, failures, applications = {}, [], []
    directory = tempfile.TemporaryDirectory()
    pages = []
    for n in range(TABS + 1):
        path = Path(directory.name) / f"page{n}.html"
        items = "".join(f"<li>item {i}</li>" for i in range(500))
        path.write_text(PAGE.format(n=n, items=items))
        pages.append(path.as_uri())

    def ms(start):
        return round((time.monotonic() - start) * 1000, 1)

    def memory_mb():
        processes = memory_modes._process_tree(os.getpid())
        return round(sum(memory_modes._pss_kb(pid) for pid, _ in processes) / 1024)

    def create_application(arguments):
        app = QApplication(arguments)
        applications.append(app)

        def wait(predicate, limit=30):
            end = time.monotonic() + limit
            while not predicate():
                if time.monotonic() > end:
                    raise TimeoutError
                app.processEvents()
                time.sleep(0.002)

        def settle(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.01)

        finished = set()

        def track(tab):
            # Only a finished load counts; a new tab's address is set before it loads.
            tab.loadFinished.connect(lambda ok, tab=tab: ok and finished.add(id(tab)))

        def measure():
            try:
                window = next(
                    w for w in app.topLevelWidgets() if isinstance(w, BrowserWindow)
                )
                results["startup"] = ms(process_start)
                first = window.tabs[0]
                track(first)
                wait(lambda: id(first) in finished)
                results["first_page"] = ms(process_start)
                settle(2)
                results["memory_one"] = memory_mb()
                opened = []
                for url in pages[1:]:
                    start = time.monotonic()
                    tab = window._new_tab(url=url, return_tab=True)
                    track(tab)
                    wait(lambda tab=tab: id(tab) in finished)
                    opened.append(ms(start))
                results["new_tab"] = statistics.median(opened)
                switches = []
                for index in [0, 5, 2, 9, 1, 7, 3, 8, 4, 6] * 2:
                    start = time.monotonic()
                    window._switch_tab(index)
                    app.processEvents()
                    switches.append(ms(start))
                results["tab_switch"] = statistics.median(switches)
                field = window.address_bar.address_input
                field.setFocus()
                field.clear()
                start = time.monotonic()
                QTest.keyClicks(field, "steed timing check 1")
                app.processEvents()
                results["typing"] = ms(start)
                field.clear()
                window.tabs[0].setFocus()
                settle(3)
                results["memory_ten"] = memory_mb()
                start = time.monotonic()
                window.close()
                results["shutdown"] = ms(start)
            except Exception as error:  # report, then let Steed close
                failures.append(repr(error))
                for widget in app.topLevelWidgets():
                    widget.close()
            QTimer.singleShot(0, app.quit)

        QTimer.singleShot(0, measure)
        return app

    with patch("pathlib.Path.home", return_value=Path(directory.name)):
        config = Config()
        config.set("home_page", pages[0])
        config.set("search_suggestions", False)
        with patch.object(main, "QApplication", side_effect=create_application) as f:
            f.setHighDpiScaleFactorRoundingPolicy = (
                QApplication.setHighDpiScaleFactorRoundingPolicy
            )
            main.main()
    if failures:
        raise SystemExit("Timing round failed: " + "; ".join(failures))
    return results


def rounds(count: int, offscreen: bool) -> list[dict]:
    env = dict(os.environ)
    if offscreen or not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    found = []
    for number in range(1, count + 1):
        print(f"Round {number} of {count}…", file=sys.stderr, flush=True)
        result = subprocess.run(
            [sys.executable, __file__, "--round"],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        if result.returncode != 0 or not lines:
            sys.stderr.write(result.stderr[-2000:])
            raise SystemExit(f"Round {number} failed")
        found.append(json.loads(lines[-1]))
    return found


def summarise(found: list[dict]) -> dict:
    return {
        name: statistics.median(r[name] for r in found)
        for name, _, _ in METRICS
        if all(name in r for r in found)
    }


def engine_description() -> str:
    from PyQt6.QtWebEngineCore import qWebEngineChromiumVersion, qWebEngineVersion

    import build_codecs

    codecs = (
        "codec build" if build_codecs.using_build(build_codecs.wheel_dir()) else "PyPI"
    )
    return f"Qt WebEngine {qWebEngineVersion()} (Chromium {qWebEngineChromiumVersion()}, {codecs})"


def show(summary: dict, earlier: dict | None) -> None:
    width = max(len(label) for _, label, _ in METRICS)
    for name, label, unit in METRICS:
        if name not in summary:
            continue
        line = f"{label:<{width}}  {summary[name]:>8.0f} {unit}"
        if earlier and name in earlier and earlier[name]:
            change = (summary[name] - earlier[name]) / earlier[name] * 100
            line += f"   was {earlier[name]:.0f} ({change:+.0f}%)"
        print(line)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--offscreen", action="store_true")
    parser.add_argument("--save", type=Path, help="write the results as JSON")
    parser.add_argument("--compare", type=Path, help="earlier results to compare")
    parser.add_argument("--round", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.round:
        print(json.dumps(run_round(args.offscreen)), flush=True)
        return 0
    sys.path.insert(0, str(ROOT))
    earlier = json.loads(args.compare.read_text())["median"] if args.compare else None
    found = rounds(args.rounds, args.offscreen)
    summary = summarise(found)
    print(engine_description())
    print(f"Median of {len(found)} rounds:")
    show(summary, earlier)
    if args.save:
        args.save.write_text(
            json.dumps(
                {"engine": engine_description(), "median": summary, "rounds": found},
                indent=2,
            )
        )
        print(f"Saved {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
