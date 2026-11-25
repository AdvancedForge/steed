"""An error in a Qt callback is reported and Steed carries on."""

import sys
import tempfile
import unittest
from pathlib import Path

from browser import error_hook


class ErrorHookTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.previous = sys.excepthook
        self.addCleanup(setattr, sys, "excepthook", self.previous)

    def test_errors_are_kept_capped_and_private(self):
        hook = error_hook.install(self.directory.name)
        self.assertIs(sys.excepthook, hook)
        for number in range(3):
            try:
                raise ValueError(f"boom {number}")
            except ValueError:
                hook(*sys.exc_info())
        log = error_hook.log_path(self.directory.name)
        text = log.read_text()
        self.assertIn("ValueError: boom 2", text)
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        log.write_bytes(b"x" * error_hook.MAX_BYTES)
        try:
            raise KeyError("latest")
        except KeyError:
            hook(*sys.exc_info())
        data = log.read_bytes()
        self.assertLessEqual(len(data), error_hook.MAX_BYTES)
        self.assertIn(b"KeyError: 'latest'", data)

    def test_a_qt_callback_error_does_not_end_the_process(self):
        import subprocess

        script = (
            "import sys, tempfile\n"
            "from browser import error_hook\n"
            "error_hook.install(tempfile.mkdtemp())\n"
            "from PyQt6.QtCore import QCoreApplication, QObject, QEvent, QTimer\n"
            "app = QCoreApplication([])\n"
            "class Bad(QObject):\n"
            "    def eventFilter(self, obj, event):\n"
            "        raise RuntimeError('in a filter')\n"
            "bad = Bad(); app.installEventFilter(bad)\n"
            "QTimer.singleShot(50, app.quit)\n"
            "app.exec()\n"
            "print('still running')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=Path(__file__).parents[1],
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("still running", result.stdout)
        self.assertIn("RuntimeError: in a filter", result.stderr)


if __name__ == "__main__":
    unittest.main()
