"""Memory modes: presets, per-setting changes, tab lifecycle and steed:memory."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QUrl
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtWebEngineCore import QWebEnginePage
from qt_test_support import application, keep_directory

import cookie_snapshot
import memory_modes
import memory_trim
from browser_window import BrowserWindow
from ui.interface_mode import native, set_native

State = QWebEnginePage.LifecycleState


class MemoryModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home_patch = patch(
            "pathlib.Path.home", return_value=Path(self.directory.name)
        )
        self.home_patch.start()
        self.window = BrowserWindow()
        self.window.resize(1000, 700)
        self.window.show()
        self.config = self.window.config
        self.settle()

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        memory_modes.apply_mode(self.config, "balanced")
        memory_modes.apply_process_policies(self.config)
        set_native(False)
        self.home_patch.stop()

    def settle(self, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def wait(self, predicate, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def changed(self):
        self.window.profile_manager.registry.settings_changed.emit()
        self.settle(0.1)

    def test_modes_are_presets_and_single_edits_become_custom(self):
        self.assertEqual(memory_modes.mode(self.config), "balanced")
        for name in memory_modes.MODES:
            memory_modes.apply_mode(self.config, name)
            self.assertEqual(memory_modes.mode(self.config), name)
            self.assertEqual(
                memory_modes.values(self.config), memory_modes.preset(name)
            )
        memory_modes.set_value(self.config, "tab_freeze_minutes", 10)
        self.assertEqual(memory_modes.mode(self.config), "custom")
        for key, value in (
            ("tab_freeze_minutes", 3),
            ("tab_freeze_minutes", True),
            ("native_interface", 1),
            ("unknown", 0),
        ):
            with self.assertRaises(ValueError):
                memory_modes.set_value(self.config, key, value)
        with self.assertRaises(ValueError):
            memory_modes.apply_mode(self.config, "turbo")
        # A corrupted stored value falls back to the default.
        self.config.set("cookie_idle_seconds", "lots")
        self.assertEqual(memory_modes.values(self.config)["cookie_idle_seconds"], 30)

    def test_modes_set_process_policies(self):
        memory_modes.apply_mode(self.config, "saver")
        self.changed()
        self.assertEqual(
            (cookie_snapshot.IDLE_MS, cookie_snapshot.WAKE_INTERVAL_MS), (5000, 20000)
        )
        self.assertTrue(memory_trim.ENABLED)
        self.assertTrue(native())
        memory_modes.apply_mode(self.config, "performance")
        self.changed()
        self.assertEqual(
            (cookie_snapshot.IDLE_MS, cookie_snapshot.WAKE_INTERVAL_MS), (0, 0)
        )
        self.assertFalse(memory_trim.ENABLED)
        self.assertFalse(native())
        # Performance preloads the palette and keeps it.
        self.wait(lambda: self.window._palette is not None)
        self.assertEqual(self.window._palette.keep_ms, -1000)
        snapshots = self.window.profile_manager.registry.snapshots.values()
        self.wait(lambda: all(not s.sleeping for s in snapshots))
        self.assertTrue(all(not s.idle.isActive() for s in snapshots))

    def test_background_tabs_freeze_then_unload_and_come_back(self):
        memory_modes.set_value(self.config, "tab_freeze_minutes", 2)
        memory_modes.set_value(self.config, "tab_discard_minutes", 15)
        self.changed()
        background = self.window._new_tab(url="about:blank#background", return_tab=True)
        self.wait(lambda: background.url().toString() == "about:blank#background")
        self.window._switch_tab(0)
        page = background.page()
        # Qt recommends one step at a time once the page is hidden.
        self.wait(lambda: page.recommendedState() != State.Active)
        background.last_active = time.monotonic() - 3 * 60
        self.window._apply_tab_lifecycle()
        self.assertEqual(page.lifecycleState(), State.Frozen)
        background.last_active = time.monotonic() - 20 * 60
        self.wait(lambda: page.recommendedState() == State.Discarded)
        self.window._apply_tab_lifecycle()
        self.assertEqual(page.lifecycleState(), State.Discarded)
        # The current tab is never touched.
        self.assertEqual(self.window.tabs[0].page().lifecycleState(), State.Active)
        self.window._switch_tab(self.window.tabs.index(background))
        self.assertEqual(page.lifecycleState(), State.Active)
        self.wait(lambda: background.url().toString() == "about:blank#background")

    def test_never_settings_leave_background_tabs_running(self):
        memory_modes.apply_mode(self.config, "performance")
        self.changed()
        background = self.window._new_tab(url="about:blank#kept", return_tab=True)
        self.window._switch_tab(0)
        background.last_active = time.monotonic() - 10 * 3600
        self.window._apply_tab_lifecycle()
        self.assertEqual(background.page().lifecycleState(), State.Active)
        self.assertFalse(self.window._lifecycle_timer.isActive())

    def test_wake_interval_delays_automatic_wakes_but_not_the_viewer(self):
        memory_modes.set_value(self.config, "cookie_wake_interval_seconds", 20)
        self.changed()
        name = self.window.profile_manager.current_profile_name
        snapshot = self.window.profile_manager.registry.snapshots[name]
        self.wait(lambda: snapshot.ready)
        snapshot.idle.setInterval(100)
        snapshot.refresh()
        self.wait(lambda: snapshot.sleeping)
        cookie = QNetworkCookie(b"throttled", b"1")
        cookie.setDomain(".throttle.example")
        cookie.setPath("/")
        self.window.cookie_manager.cookie_store.setCookie(
            cookie, QUrl("http://throttle.example")
        )
        self.settle(1)
        self.assertTrue(snapshot.sleeping)
        self.assertTrue(snapshot.deferred.isActive())
        # The cookie viewer asks explicitly and is never delayed.
        snapshot.idle.setInterval(60000)
        self.window.cookie_manager.load_all_cookies()
        self.wait(lambda: not snapshot.sleeping and snapshot.ready)
        self.assertFalse(snapshot.deferred.isActive())

    def test_memory_page_switches_modes_reports_usage_and_frees(self):
        tab = self.window._new_tab(url="steed:memory", return_tab=True)
        page = tab.workspace_controller
        self.wait(lambda: page.surface.ready)

        def send(action, **extra):
            page.command(
                {
                    "context": page.context,
                    "sequence": page.sequence + 1,
                    "action": action,
                    **extra,
                }
            )

        send("mode", mode="saver")
        self.assertEqual(memory_modes.mode(self.config), "saver")
        self.assertEqual(self.window._memory["tab_discard_minutes"], 15)
        send("set", key="tab_discard_minutes", value=60)
        self.assertEqual(memory_modes.mode(self.config), "custom")
        send("set", key="tab_discard_minutes", value=7)
        self.assertIn("listed", page.error)
        send("free")
        self.assertIn("cookie inspector", page.notice)
        usage = memory_modes.usage([self.window])
        self.assertTrue(usage["available"])
        self.assertGreater(usage["total_mb"], 0)
        self.assertIn("renderer", usage["processes"])
        # Stale or forged requests are ignored.
        before = memory_modes.values(self.config)
        page.command(
            {"context": "forged", "sequence": 99, "action": "mode", "mode": "balanced"}
        )
        self.assertEqual(memory_modes.values(self.config), before)

    def test_tab_strip_marks_dormant_tabs_and_keep_loaded_exempts_one(self):
        memory_modes.set_value(self.config, "tab_freeze_minutes", 2)
        self.changed()
        kept = self.window._new_tab(url="about:blank#kept", return_tab=True)
        other = self.window._new_tab(url="about:blank#other", return_tab=True)
        self.window._switch_tab(0)
        self.window._set_keep_loaded(kept, True)
        for tab in (kept, other):
            self.wait(lambda tab=tab: tab.page().recommendedState() != State.Active)
            tab.last_active = time.monotonic() - 3600
        self.window._apply_tab_lifecycle()
        self.assertEqual(kept.page().lifecycleState(), State.Active)
        self.assertEqual(other.page().lifecycleState(), State.Frozen)
        records = self.window.tab_bar.records
        self.wait(
            lambda: records[self.window.tabs.index(other)]["lifecycle"] == "frozen"
        )
        self.assertEqual(records[self.window.tabs.index(kept)]["lifecycle"], "active")
        self.assertTrue(records[self.window.tabs.index(kept)]["keep_loaded"])
        self.window._switch_tab(self.window.tabs.index(other))
        self.wait(
            lambda: records[self.window.tabs.index(other)]["lifecycle"] == "active"
        )
        # Keeping a frozen tab loaded wakes it.
        self.window._switch_tab(0)
        other.page().setLifecycleState(State.Frozen)
        self.window._set_keep_loaded(other, True)
        self.assertEqual(other.page().lifecycleState(), State.Active)


class PressureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_low_memory_starts_below_ten_percent_and_ends_above_twenty(self):
        share = [0.5]
        monitor = memory_modes.PressureMonitor(lambda: (share[0] * 100, 100))
        events = []
        monitor.changed.connect(events.append)
        for value, low in ((0.5, False), (0.09, True), (0.15, True), (0.21, False)):
            share[0] = value
            monitor.check()
            self.assertEqual(monitor.low, low)
        self.assertEqual(events, [True, False])
        unreadable = memory_modes.PressureMonitor(lambda: None)
        unreadable.check()
        self.assertFalse(unreadable.low)

    def test_auto_saver_uses_saver_values_without_changing_settings(self):
        from config import Config

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("pathlib.Path.home", return_value=Path(directory)),
        ):
            config = Config()
            memory_modes.apply_mode(config, "balanced")
            config.set("native_interface", False)
            low = memory_modes.monitor()
            previous = low.low
            try:
                low.low = True
                current = memory_modes.effective(config)
                self.assertEqual(current["tab_discard_minutes"], 15)
                self.assertEqual(current["cookie_wake_interval_seconds"], 20)
                self.assertFalse(current["native_interface"])
                self.assertEqual(memory_modes.mode(config), "balanced")
                memory_modes.set_value(config, "auto_saver", False)
                self.assertEqual(
                    memory_modes.effective(config)["tab_discard_minutes"], 240
                )
            finally:
                low.low = previous


if __name__ == "__main__":
    unittest.main()
