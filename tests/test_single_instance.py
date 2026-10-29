"""A second launch hands its request to the browser holding the data directory."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtNetwork import QLocalSocket
from qt_test_support import application, keep_directory

import main
from browser_window import BrowserWindow
from single_instance import InstanceServer, launch_urls, request_existing

ROOT = Path(__file__).resolve().parent.parent
CLIENT = """
import sys
from PyQt6.QtCore import QCoreApplication
from single_instance import request_existing
app = QCoreApplication([])
sys.exit(0 if request_existing(sys.argv[1], sys.argv[2:]) else 3)
"""


class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.config_dir = Path(self.directory.name) / "data"
        self.config_dir.mkdir()
        self.received = []
        self.server = InstanceServer(self.config_dir)
        self.server.open_requested.connect(self.received.append)

    def tearDown(self):
        self.server.close()

    def launch(self, *urls):
        process = subprocess.Popen(
            [sys.executable, "-c", CLIENT, str(self.config_dir), *urls],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        deadline = time.monotonic() + 15
        while process.poll() is None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        return process.wait(1)

    def test_second_launch_is_accepted_with_only_web_addresses(self):
        self.assertTrue(self.server.listening)
        self.assertEqual(
            self.launch("https://example.org/a", "file:///etc/passwd", "--flag"), 0
        )
        self.assertEqual(self.received, [["https://example.org/a"]])
        self.assertEqual(self.launch(), 0)
        self.assertEqual(self.received[-1], [])

    def test_without_a_running_browser_the_launch_is_not_accepted(self):
        self.server.close()
        self.assertFalse(request_existing(self.config_dir, [], timeout_ms=200))

    def test_malformed_or_oversized_requests_are_dropped(self):
        for payload in (b"not json\n", b'{"version": 2, "urls": []}\n', b"x" * 70000):
            socket = QLocalSocket()
            socket.connectToServer(self.server.server.fullServerName())
            self.assertTrue(socket.waitForConnected(1000))
            socket.write(payload)
            socket.flush()
            deadline = time.monotonic() + 2
            while (
                socket.state() == QLocalSocket.LocalSocketState.ConnectedState
                and time.monotonic() < deadline
            ):
                self.app.processEvents()
                socket.waitForDisconnected(10)
            self.assertNotEqual(
                socket.state(), QLocalSocket.LocalSocketState.ConnectedState
            )
        self.assertEqual(self.received, [])

    def test_launch_url_filter_is_bounded(self):
        self.assertEqual(len(launch_urls(["https://example.org"] * 30)), 20)
        self.assertEqual(launch_urls(["javascript:alert(1)", "http://"]), [])


class LaunchRequestTests(unittest.TestCase):
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
        self.window.show()

    def tearDown(self):
        self.window.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.home_patch.stop()

    def test_urls_open_as_tabs_and_a_bare_launch_opens_a_window(self):
        before = len(self.window.tabs)
        main.open_launch_request(["https://example.org/"])
        self.assertEqual(len(self.window.tabs), before + 1)
        main.open_launch_request([])
        self.assertEqual(len(self.window._child_windows), 1)
        self.assertTrue(self.window._child_windows[0].isVisible())


if __name__ == "__main__":
    unittest.main()
