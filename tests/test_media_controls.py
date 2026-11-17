"""Media controls: state, play/pause, the toolbar button, Mini Player and MPRIS."""

import io
import math
import struct
import tempfile
import threading
import time
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser import webengine
from browser.browser_window import BrowserWindow
from ui import media_controls


def tone():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(n / 8000 * 440 * 2 * math.pi)))
            for n in range(8000 * 20)
        )
        out.writeframes(frames)
    return buffer.getvalue()


PAGE = b"<title>Player</title><video id=v src='/tone.wav' loop></video>"


class Server(BaseHTTPRequestHandler):
    sound = b""

    def do_GET(self):
        body = self.sound if self.path == "/tone.wav" else PAGE
        self.send_response(200)
        self.send_header(
            "Content-Type", "audio/wav" if self.path == "/tone.wav" else "text/html"
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        Server.sound = tone()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.window = BrowserWindow()
        self.window.resize(900, 600)
        self.window.show()
        self.media = media_controls.controller()

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

    def js(self, tab, source):
        out = []
        webengine.run_script(tab.page(), source, out.append, world=webengine.PAGE_WORLD)
        self.wait(lambda: out)
        return out[0]

    def playing_tab(self):
        tab = self.window._new_tab(url=self.origin + "/", return_tab=True)
        self.wait(lambda: tab.title() == "Player" and not tab.page().isLoading())
        self.js(tab, "document.getElementById('v').play().then(() => 1, () => 0); 1")
        self.wait(lambda: self.js(tab, "!document.getElementById('v').paused"))
        self.media.refresh_tab(self.window, tab)
        self.wait(lambda: self.media.tab is tab and self.media.playing)
        return tab

    def test_state_toggle_and_toolbar(self):
        tab = self.playing_tab()
        self.assertEqual(self.media.status()[0], "Playing")
        button = self.window.address_bar.media_btn
        self.assertFalse(button.isHidden())
        self.assertTrue(button.text().startswith("⏸"))
        self.media.toggle()
        self.wait(lambda: not self.media.playing)
        self.assertTrue(self.js(tab, "document.getElementById('v').paused"))
        self.assertTrue(button.text().startswith("▶"))
        self.media.play()
        self.wait(lambda: self.media.playing)
        self.window._close_tab(self.window.tabs.index(tab))
        self.assertIsNone(self.media.tab)
        self.assertTrue(button.isHidden())

    def test_mini_player_floats_the_tab_and_selecting_it_brings_it_back(self):
        tab = self.playing_tab()
        player = self.window.open_mini_player(tab)
        self.assertIs(tab.mini_player, player)
        self.assertIs(tab.parent(), player)
        self.assertIsNot(self.window.tabs[self.window.current_tab_index], tab)
        self.wait(
            lambda: self.js(tab, "document.querySelector('[data-steed-mini]') !== null")
        )
        self.window._switch_tab(self.window.tabs.index(tab))
        self.wait(lambda: tab.parent() is self.window.tab_container)
        self.assertIsNone(tab.mini_player)
        self.assertTrue(tab.isVisible())
        self.wait(
            lambda: self.js(tab, "document.querySelector('[data-steed-mini]') === null")
        )

    def bus(self, *arguments):
        """Ask over the session bus from another process, as the desktop does."""
        import shutil
        import subprocess

        if shutil.which("gdbus") is None:
            self.skipTest("gdbus is not installed")
        process = subprocess.Popen(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                self.media.mpris.service,
                "--object-path",
                "/org/mpris/MediaPlayer2",
                *arguments,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.wait(lambda: process.poll() is not None)
        return process.stdout.read()

    def status(self):
        return self.bus(
            "--method",
            "org.freedesktop.DBus.Properties.Get",
            "org.mpris.MediaPlayer2.Player",
            "PlaybackStatus",
        )

    def test_desktop_media_keys_through_mpris(self):
        if not media_controls.mpris_available():
            self.skipTest("No session bus")
        tab = self.playing_tab()
        self.media.start_desktop()
        self.assertTrue(self.media.mpris.registered)
        self.assertIn("Playing", self.status())
        identity = self.bus(
            "--method",
            "org.freedesktop.DBus.Properties.Get",
            "org.mpris.MediaPlayer2",
            "Identity",
        )
        self.assertIn("Steed", identity)
        self.bus("--method", "org.mpris.MediaPlayer2.Player.PlayPause")
        self.wait(lambda: not self.media.playing)
        self.assertTrue(self.js(tab, "document.getElementById('v').paused"))
        self.assertIn("Paused", self.status())

    def test_the_desktop_hears_about_changes(self):
        import subprocess

        if not media_controls.mpris_available():
            self.skipTest("No session bus")
        tab = self.playing_tab()
        self.media.start_desktop()
        self.status()  # gdbus present; skips otherwise
        monitor = subprocess.Popen(
            ["gdbus", "monitor", "--session", "--dest", self.media.mpris.service],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            end = time.monotonic() + 1
            while time.monotonic() < end:
                self.app.processEvents()
                time.sleep(0.02)
            self.media.toggle()
            self.wait(lambda: not self.media.playing)
            end = time.monotonic() + 1
            while time.monotonic() < end:
                self.app.processEvents()
                time.sleep(0.02)
        finally:
            monitor.terminate()
        output = monitor.communicate(timeout=10)[0]
        self.assertIn("PropertiesChanged", output)
        self.assertIn("'PlaybackStatus': <'Paused'>", output)
        self.assertIn("Player", output)
        self.assertTrue(tab is self.media.tab)


if __name__ == "__main__":
    unittest.main()
