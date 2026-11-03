"""steed:features and the warning when pip replaces the codec build."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

import build_codecs
import engine_features
from browser_window import BrowserWindow


def by_name(features):
    return {feature["name"]: feature for feature in features}


class FeatureResultTests(unittest.TestCase):
    def test_answers_pair_with_features_and_bad_answers_count_as_missing(self):
        answers = "[" + ",".join(["true"] * len(engine_features.FEATURES)) + "]"
        features = engine_features.results(answers)
        self.assertTrue(all(f["supported"] and not f["reason"] for f in features))
        for raw in (None, "not json", "[true]", '{"a": 1}'):
            features = engine_features.results(raw)
            self.assertEqual(len(features), len(engine_features.FEATURES))
            self.assertFalse(any(f["supported"] for f in features))
        reasons = by_name(engine_features.results(None))
        self.assertIn("build_codecs.py", reasons["H.264 video"]["reason"])
        self.assertIn("upgrade brings", reasons["Temporal (dates and times)"]["reason"])
        self.assertIn("will not add", reasons["WebUSB"]["reason"])


class FeaturePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.show()

    def tearDown(self):
        self.window.close()
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

    def test_page_tests_features_in_a_web_page_and_checks_again(self):
        tab = self.window._new_tab(url="steed:features", return_tab=True)
        page = tab.workspace_controller
        self.wait(lambda: page.surface.ready and page.features is not None)
        features = by_name(page.features)
        self.assertEqual(len(features), len(engine_features.FEATURES))
        # Present in every Qt WebEngine Steed supports.
        for name in ("VP9 video", "Opus audio", "Popover", "Security keys (WebAuthn)"):
            self.assertTrue(features[name]["supported"], name)
        built = build_codecs.using_build(build_codecs.wheel_dir(), thorough=False)
        self.assertEqual(features["H.264 video"]["supported"], built)
        self.assertFalse(features["WebUSB"]["supported"])
        summary = engine_features.engine_summary()
        self.assertEqual(summary["codecs"], "built" if built else "pypi")
        self.assertRegex(summary["chromium"], r"^\d+\.")
        page.command({"context": page.context, "sequence": 1, "action": "check"})
        self.assertIsNotNone(page.probe)
        self.wait(lambda: page.probe is None)
        self.assertEqual(len(page.features), len(engine_features.FEATURES))

    def test_warning_only_after_pip_replaced_the_build_until_ignored(self):
        with patch.object(build_codecs, "replaced_by_pip", return_value=False):
            self.window._warn_codecs_replaced()
        self.assertIsNone(getattr(self.window, "codec_warning", None))
        with patch.object(build_codecs, "replaced_by_pip", return_value=True):
            self.window._warn_codecs_replaced()
            box = self.window.codec_warning
            self.assertIn("build_codecs.py install", box.text())
            before = len(self.window.tabs)
            next(b for b in box.buttons() if b.text() == "Open steed:features").click()
            self.wait(lambda: len(self.window.tabs) == before + 1)
            self.window.codec_warning = None
            self.window._warn_codecs_replaced()
            box = self.window.codec_warning
            next(b for b in box.buttons() if "Don't remind" in b.text()).click()
            self.assertEqual(
                self.window.config.get("codec_warning_ignored"),
                build_codecs.qt_version(),
            )
            self.window.codec_warning = None
            self.window._warn_codecs_replaced()
        self.assertIsNone(self.window.codec_warning)


if __name__ == "__main__":
    unittest.main()
