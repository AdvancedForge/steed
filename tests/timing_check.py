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
    ("scroll_frame", "Scrolling: slowest 5% of frames", "ms"),
    ("scroll_slow", "Scrolling: frames over 25 ms, of ~60", "frames"),
    ("wake_frozen", "Switch to a frozen tab until it responds", "ms"),
    ("wake_unloaded", "Switch to an unloaded tab until it reloads", "ms"),
    ("memory_one", "Memory, one tab", "MB"),
    ("memory_idle", "Memory, one tab once idle", "MB"),
    ("memory_ten", f"Memory, {TABS} tabs", "MB"),
    ("restored_first_page", f"Restart with {TABS + 1} tabs: first page", "ms"),
    ("restored_memory", f"Restart with {TABS + 1} tabs: memory", "MB"),
    ("closed", "Close the window until it disappears", "ms"),
    ("shutdown", "Close the window and clean up", "ms"),
)
# Scrolls the page for one second, one step per frame, and reports frame gaps.
SCROLL = """(() => { window.__frames = null; const gaps = []; let last = null;
const end = performance.now() + 1000;
function step(now) { if (last !== null) gaps.push(now - last); last = now;
  window.scrollBy(0, 40);
  if (now < end) requestAnimationFrame(step); else window.__frames = gaps; }
requestAnimationFrame(step); return true; })()"""
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


def run_round(home: Path, restored: bool, load_all: bool) -> dict:
    """One Steed session in home; prints its figures as JSON.

    The first session ends with its tabs saved; a restored one starts from them.
    """
    process_start = time.monotonic() - seconds_since_process_start()
    sys.path.insert(0, str(ROOT))
    from PyQt6.QtCore import QEvent, QObject, QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    import main
    from browser import memory_modes, webengine
    from browser.browser_window import BrowserWindow
    from browser.config import Config

    class HideWatch(QObject):
        """Notes when the window leaves the screen, before cleanup finishes."""

        def __init__(self, window, seen):
            super().__init__()
            self.seen = seen
            window.installEventFilter(self)

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Hide:
                self.seen()
            return False

    results, failures, applications = {}, [], []
    pages = []
    for n in range(TABS + 1):
        path = home / f"page{n}.html"
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
                if restored:
                    results["restored_first_page"] = ms(process_start)
                    settle(3)
                    results["restored_memory"] = memory_mb()
                    window.close()
                    QTimer.singleShot(0, app.quit)
                    return
                results["first_page"] = ms(process_start)
                settle(2)
                results["memory_one"] = memory_mb()
                # Cookie inspection runs for a while after starting, then sleeps
                # (30 s in Balanced); idle figures are what Steed settles at.
                from browser import cookie_snapshot

                def inspectors_asleep():
                    return all(
                        s.closed or s.sleeping for s in list(cookie_snapshot._SNAPSHOTS)
                    )

                wait(inspectors_asleep, limit=120)
                settle(3)
                results["memory_idle"] = memory_mb()
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
                settle(1)

                def script(source):
                    out = []
                    webengine.run_script(
                        window.tabs[0].page(),
                        source,
                        out.append,
                        world=webengine.PAGE_WORLD,
                    )
                    wait(lambda: out)
                    return out[0]

                window._switch_tab(0)
                script(SCROLL)
                wait(lambda: script("window.__frames !== null"))
                gaps = sorted(script("window.__frames"))
                results["scroll_frame"] = round(gaps[int(len(gaps) * 0.95)], 1)
                results["scroll_slow"] = sum(gap > 25 for gap in gaps)
                # Background tabs, put to sleep the way memory modes do.
                frozen, unloaded = window.tabs[3], window.tabs[4]
                webengine.set_tab_state(frozen.page(), webengine.FROZEN)
                webengine.set_tab_state(unloaded.page(), webengine.UNLOADED)
                wait(lambda: webengine.tab_state(frozen.page()) == webengine.FROZEN)
                wait(lambda: webengine.tab_state(unloaded.page()) == webengine.UNLOADED)
                start = time.monotonic()
                window._switch_tab(3)
                answered = []
                webengine.run_script(
                    frozen.page(), "1", answered.append, world=webengine.PAGE_WORLD
                )
                wait(lambda: answered)
                results["wake_frozen"] = ms(start)
                finished.discard(id(unloaded))
                start = time.monotonic()
                window._switch_tab(4)
                wait(lambda: id(unloaded) in finished)
                results["wake_unloaded"] = ms(start)
                window._switch_tab(0)
                settle(3)
                results["memory_ten"] = memory_mb()
                start = time.monotonic()
                hidden = HideWatch(
                    window, lambda: results.setdefault("closed", ms(start))
                )
                window.close()
                results["shutdown"] = ms(start)
                results.setdefault("closed", results["shutdown"])
                del hidden
            except Exception as error:  # report, then let Steed close
                failures.append(repr(error))
                for widget in app.topLevelWidgets():
                    widget.close()
            QTimer.singleShot(0, app.quit)

        QTimer.singleShot(0, measure)
        return app

    with patch("pathlib.Path.home", return_value=home):
        config = Config()
        config.set("home_page", pages[0])
        config.set("search_suggestions", False)
        config.set("restore_tabs_lazily", not load_all)
        with patch.object(main, "QApplication", side_effect=create_application) as f:
            f.setHighDpiScaleFactorRoundingPolicy = (
                QApplication.setHighDpiScaleFactorRoundingPolicy
            )
            main.main()
    if failures:
        raise SystemExit("Timing round failed: " + "; ".join(failures))
    return results


def rounds(count: int, offscreen: bool, load_all: bool) -> list[dict]:
    env = dict(os.environ)
    if offscreen or not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        env["QT_QPA_PLATFORM"] = "offscreen"
        env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    found = []
    for number in range(1, count + 1):
        print(f"Round {number} of {count}…", file=sys.stderr, flush=True)
        merged = {}
        with tempfile.TemporaryDirectory() as home:
            common = ["--load-all-tabs"] if load_all else []
            for extra in (common, [*common, "--restored"]):
                result = subprocess.run(
                    [sys.executable, __file__, "--round", "--home", home, *extra],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                lines = [
                    line for line in result.stdout.splitlines() if line.startswith("{")
                ]
                if result.returncode != 0 or not lines:
                    sys.stderr.write(result.stderr[-2000:])
                    raise SystemExit(f"Round {number} failed")
                merged.update(json.loads(lines[-1]))
        found.append(merged)
    return found


def summarise(found: list[dict]) -> dict:
    return {
        name: statistics.median(r[name] for r in found)
        for name, _, _ in METRICS
        if all(name in r for r in found)
    }


def engine_description() -> str:
    from PyQt6.QtWebEngineCore import qWebEngineChromiumVersion, qWebEngineVersion

    from browser import build_codecs

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
    parser.add_argument(
        "--load-all-tabs",
        action="store_true",
        help="load every restored tab at startup (Performance mode's choice)",
    )
    parser.add_argument("--home", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--restored", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.round:
        print(
            json.dumps(run_round(args.home, args.restored, args.load_all_tabs)),
            flush=True,
        )
        return 0
    sys.path.insert(0, str(ROOT))
    earlier = json.loads(args.compare.read_text())["median"] if args.compare else None
    load = os.getloadavg()[0]
    if load > (os.cpu_count() or 1) * 0.5:
        print(
            f"Warning: this computer is busy (load {load:.1f} on {os.cpu_count()} "
            "cores); the figures will be slower and vary more than usual.",
            file=sys.stderr,
        )
    found = rounds(args.rounds, args.offscreen, args.load_all_tabs)
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
