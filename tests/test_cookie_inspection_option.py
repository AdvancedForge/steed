"""Cookie inspection only when needed, and settings added in newer versions."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import memory_modes
from browser.browser_window import BrowserWindow
from browser.config import Config


class StoredModeTests(unittest.TestCase):
    def config(self, values):
        class Store:
            def __init__(self):
                self.data = dict(values)

            def get(self, key, default=None):
                return self.data.get(key, default)

        return Store()

    def test_a_new_setting_follows_the_mode_the_others_match(self):
        saver = memory_modes.preset("saver")
        old = {k: v for k, v in saver.items() if k != "cookie_inspection"}
        config = self.config(old)
        self.assertEqual(memory_modes.mode(config), "saver")
        self.assertEqual(memory_modes.values(config)["cookie_inspection"], "needed")
        self.assertEqual(memory_modes.mode(self.config({})), "balanced")
        self.assertEqual(
            memory_modes.values(self.config({}))["cookie_inspection"], "always"
        )


class OnDemandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        Config().set("cookie_inspection", "needed")
        self.window = BrowserWindow()
        self.window.show()
        self.registry = self.window.profile_manager.registry
        self.windows = [self.window]

    def tearDown(self):
        for window in reversed(self.windows):
            window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def test_only_a_sharing_profile_starts_inspection(self):
        self.assertEqual(self.window.profile_manager.current_profile_name, "global")
        self.assertEqual(set(self.registry.snapshots), set())
        self.assertEqual(set(self.registry.deferred_inspection), {"global", "search"})
        self.assertEqual(self.window.cookie_manager.status, "Live cookie updates")
        self.window.profile_manager.create_profile("work")
        self.window._switch_to_profile("work")
        self.wait(lambda: {"work", "global"} <= set(self.registry.snapshots))
        self.assertNotIn("search", self.registry.snapshots)
        self.assertIsNotNone(self.window.cookie_manager.snapshot)

    def test_incognito_needs_none_and_switching_back_starts_the_rest(self):
        self.window._switch_to_profile("search")
        self.assertNotIn("search", self.registry.snapshots)
        memory_modes.set_value(self.window.config, "cookie_inspection", "always")
        self.registry.settings_changed.emit()
        self.wait(lambda: {"global", "search"} <= set(self.registry.snapshots))
        self.assertEqual(self.registry.deferred_inspection, {})


import test_browser  # noqa: E402


class SharingOnDemandTests(test_browser.BrowserTests):
    """Global cookies still reach a sharing profile when inspection starts late."""

    def setUp(self):
        self.on_demand = patch(
            "browser.profile_manager.ProfileRegistry._inspect_on_demand",
            return_value=True,
        )
        self.on_demand.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.on_demand.stop()


SHARING_TESTS = (
    "test_native_global_cookies_and_local_override",
    "test_native_host_only_cookie_scope",
    "test_native_global_update_and_logout_delete_inherited_cookie",
)
for _name in [n for n in dir(SharingOnDemandTests) if n.startswith("test_")]:
    if _name not in SHARING_TESTS:
        setattr(SharingOnDemandTests, _name, None)


if __name__ == "__main__":
    unittest.main()
