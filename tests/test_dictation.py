"""Dictation: the engines, model downloads, push-to-talk keys and putting the
words where the cursor is. No microphone, model or network is used: a fake
recorder supplies "audio" and fake workers and a local server transcribe it."""

import io
import json
import struct
import tarfile
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QLineEdit
from qt_test_support import WAIT_SECONDS, application, keep_directory

from browser_window import BrowserWindow
from services import dictation

FAKE_WORKER = Path(__file__).parent / "fixtures" / "fake_dictation_worker.py"


def spoken(text, seconds=1.0):
    """Fake audio: the words, padded with silence (16 kHz, 16-bit, mono)."""
    data = text.encode()
    return data + b"\0" * max(0, int(32000 * seconds) - len(data))


class FakeRecorder(QObject):
    level = pyqtSignal(float)
    failed = pyqtSignal(str)
    audio = b""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source = None
        self.rate, self.format, self.channels = 16000, "int16", 1

    def start(self):
        self.source = object()
        self.level.emit(0.5)
        return True

    def bytes_per_second(self):
        return 32000

    def snapshot(self):
        return FakeRecorder.audio

    def stop(self):
        self.source = None
        return FakeRecorder.audio


def wait(app, predicate):
    end = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    raise AssertionError("Timed out")


class PartsTests(unittest.TestCase):
    def test_settings_fall_back_when_values_are_unknown(self):
        chosen = dictation.settings(
            {
                "dictation_engine": "telepathy",
                "dictation_model": "huge",
                "dictation_url": "ftp://x",
                "dictation_language": "much-too-long",
            }
        )
        self.assertEqual(chosen["engine"], "local")
        self.assertEqual(chosen["model"], dictation.DEFAULT_MODEL)
        self.assertEqual(chosen["url"], dictation.DEFAULT_URL)
        self.assertEqual(chosen["language"], "")
        self.assertTrue(chosen["live"])

    def test_services_need_https_except_on_this_computer(self):
        self.assertEqual(
            dictation.check_url("http://localhost:8000/v1/audio/transcriptions"),
            "http://localhost:8000/v1/audio/transcriptions",
        )
        dictation.check_url("https://api.groq.com/openai/v1/audio/transcriptions")
        for bad in ("http://example.com/v1", "file:///etc/passwd", "nonsense"):
            with self.assertRaises(ValueError):
                dictation.check_url(bad)

    def test_spoken_line_breaks(self):
        self.assertEqual(
            dictation.tidy(" Dear Ada, new line. Thanks. New paragraph, Bye. "),
            "Dear Ada,\nThanks.\n\nBye.",
        )

    def test_wav_keeps_the_first_channel_as_16_bit(self):
        stereo = struct.pack("<4h", 1000, -5, -2000, -5)
        made = dictation.wav(stereo, 16000, "int16", 2)
        self.assertEqual(made[:4], b"RIFF")
        self.assertEqual(struct.unpack("<HHI", made[20:28]), (1, 1, 16000))
        self.assertEqual(made[44:], struct.pack("<2h", 1000, -2000))
        floats = struct.pack("<2f", 0.5, -1.0)
        self.assertEqual(
            dictation.wav(floats, 8000, "float32", 1)[44:],
            struct.pack("<2h", 16383, -32767),
        )
        self.assertAlmostEqual(dictation.peak(stereo, "int16"), 2000 / 32768)


class WorkerTests(unittest.TestCase):
    """The real dictation_worker.py, without a model."""

    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def test_bad_requests_are_answered_not_fatal(self):
        import subprocess
        import sys

        worker = Path(__file__).parents[1] / "dictation_worker.py"
        lines = [
            "not json",
            json.dumps({"op": "decode", "id": 4, "audio": ""}),
            json.dumps({"op": "load", "kind": "nope", "dir": "/nowhere"}),
        ]
        done = subprocess.run(
            [sys.executable, str(worker)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            timeout=60,
        )
        replies = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual(len(replies), 3)
        self.assertIsNone(replies[0]["id"])
        self.assertEqual(replies[1]["id"], 4)
        self.assertIn("No model is loaded", replies[1]["error"])
        self.assertTrue(all("error" in reply for reply in replies))

    def test_the_engine_answers_in_order_and_reports_a_broken_model(self):
        engine = dictation.LocalEngine()
        engine.WORKER = FAKE_WORKER
        heard, failed = [], []
        engine.result.connect(lambda i, text: heard.append((i, text)))
        engine.failed.connect(failed.append)
        engine.ensure("/models/good", "moonshine_v2")
        engine.transcribe(1, spoken("one"), 16000, "int16", 1)
        engine.transcribe(2, spoken("two"), 16000, "int16", 1)
        self.assertEqual(engine.pending(), 3)  # The load, then two in turn.
        wait(self.app, lambda: len(heard) == 2)
        self.assertEqual(heard, [(1, "one"), (2, "two")])
        self.assertEqual(engine.pending(), 0)
        engine.ensure("/models/broken", "moonshine_v2")
        wait(self.app, lambda: failed)
        self.assertIn("no model", failed[0])
        self.assertIsNone(engine.loaded)  # The next dictation tries again.
        engine.stop()
        self.assertIsNone(engine.process)


def archive(path, inner, files):
    with tarfile.open(path, "w:bz2") as bundle:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{inner}/{name}" if inner else name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "releases").mkdir()
        self.url = patch.object(
            dictation,
            "MODELS_URL",
            QUrl.fromLocalFile(str(self.root / "releases")).toString() + "/",
        )
        self.url.start()
        self.addCleanup(self.url.stop)
        self.models = dictation.Models(self.root / "dictation-models")
        self.done = []
        self.models.finished.connect(lambda model, error: self.done.append(error))

    def test_a_model_downloads_unpacks_and_can_be_removed(self):
        name = dictation.MODELS["moonshine-tiny"]["archive"]
        archive(
            self.root / "releases" / f"{name}.tar.bz2",
            name,
            {"tokens.txt": b"a 0\n", "test_wavs/0.wav": b"RIFF"},
        )
        self.assertFalse(self.models.installed("moonshine-tiny"))
        self.models.download("moonshine-tiny")
        self.assertTrue(self.models.downloading("moonshine-tiny"))
        wait(self.app, lambda: self.done)
        self.assertEqual(self.done, [""])
        self.assertTrue(self.models.installed("moonshine-tiny"))
        target = self.models.path("moonshine-tiny")
        self.assertEqual((target / "tokens.txt").read_bytes(), b"a 0\n")
        self.assertFalse((target / "test_wavs").exists())
        # No partial downloads or staging folders are left behind.
        self.assertEqual(
            [p.name for p in (self.root / "dictation-models").iterdir()], [name]
        )
        self.models.remove("moonshine-tiny")
        self.assertFalse(self.models.installed("moonshine-tiny"))

    def test_an_archive_cannot_write_outside_the_models_folder(self):
        name = dictation.MODELS["moonshine-tiny"]["archive"]
        archive(
            self.root / "releases" / f"{name}.tar.bz2",
            "",
            {f"{name}/tokens.txt": b"x", "../escaped.txt": b"gotcha"},
        )
        self.models.download("moonshine-tiny")
        wait(self.app, lambda: self.done)
        self.assertIn("could not be unpacked", self.done[0])
        self.assertFalse((self.root / "escaped.txt").exists())
        self.assertFalse(self.models.installed("moonshine-tiny"))

    def test_a_missing_download_fails_cleanly(self):
        self.models.download("whisper-base")
        wait(self.app, lambda: self.done)
        self.assertIn("could not be downloaded", self.done[0])
        self.assertFalse(self.models.downloading("whisper-base"))
        self.assertEqual(list((self.root / "dictation-models").iterdir()), [])


class Service(BaseHTTPRequestHandler):
    seen = []
    status = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Service.seen.append((dict(self.headers), body))
        reply = json.dumps({"text": " Hello from the service. "}).encode()
        self.send_response(Service.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


class OnlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()
        cls.server = HTTPServer(("127.0.0.1", 0), Service)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/v1/audio/transcriptions"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Service.seen.clear()
        Service.status = 200
        self.engine = dictation.OnlineEngine()
        self.heard, self.failed = [], []
        self.engine.result.connect(lambda i, text: self.heard.append((i, text)))
        self.engine.failed.connect(self.failed.append)

    def test_audio_goes_as_a_wav_upload_with_the_model_and_key(self):
        self.engine.transcribe(
            7, spoken("", 0.5), 16000, "int16", 1, self.url, "whisper-1", "sk-test"
        )
        wait(self.app, lambda: self.heard)
        self.assertEqual(self.heard, [(7, "Hello from the service.")])
        headers, body = Service.seen[0]
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertIn("multipart/form-data", headers["Content-Type"])
        self.assertIn(b'name="model"\r\n\r\nwhisper-1', body)
        self.assertIn(b'filename="dictation.wav"', body)
        self.assertIn(b"RIFF", body)

    def test_a_refused_key_is_reported(self):
        Service.status = 401
        self.engine.transcribe(1, spoken("", 0.5), 16000, "int16", 1, self.url, "m", "")
        wait(self.app, lambda: self.failed)
        self.assertEqual(self.failed, ["The dictation service refused the key."])
        self.assertNotIn("Authorization", Service.seen[0][0])


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        keep_directory(self.directory)
        self.home = patch("pathlib.Path.home", return_value=Path(self.directory.name))
        self.home.start()
        self.recorder = patch.object(dictation, "Recorder", FakeRecorder)
        self.recorder.start()
        self.worker = patch.object(dictation.LocalEngine, "WORKER", FAKE_WORKER)
        self.worker.start()
        self.window = BrowserWindow()
        self.window.show()
        self.window.activateWindow()
        self.tab = self.window.tabs[self.window.current_tab_index]
        self.controller = self.window.dictation
        models = Path(self.directory.name) / ".custom_browser" / "dictation-models"
        (models / dictation.MODELS[dictation.DEFAULT_MODEL]["archive"]).mkdir(
            parents=True
        )
        (models / dictation.MODELS[dictation.DEFAULT_MODEL]["archive"] / "x").touch()
        self.inserted = []
        self.controller.inserted.connect(
            lambda where, text: self.inserted.append((where, text))
        )

    def tearDown(self):
        self.window.close()
        dictation.stop_shared()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.worker.stop()
        self.recorder.stop()
        self.home.stop()

    def wait(self, predicate):
        return wait(self.app, predicate)

    def page(self, html):
        self.tab.setHtml(html, QUrl("https://example.org/form"))
        self.wait(lambda: self.tab.url().toString() == "https://example.org/form")
        self.wait(lambda: self.tab.title() not in ("", "example.org/form"))

    def run_js(self, script):
        result = []
        self.tab.page().runJavaScript(script, result.append)
        self.wait(lambda: result)
        return result[0]

    def key(self, kind, key, modifiers, repeat=False, target=None):
        target = target or self.window.address_bar.address_input
        event = QKeyEvent(kind, key, modifiers, "", repeat)
        return QApplication.sendEvent(target, event)

    def test_words_go_into_the_focused_page_field_with_spacing(self):
        self.page(
            "<title>Form</title><textarea id=t>Hello</textarea>"
            "<input id=p type=password>"
            "<script>t.addEventListener('input', () => document.title = 'Typed');"
            "t.focus(); t.setSelectionRange(5, 5)</script>"
        )
        self.controller.target = ("web", self.tab)
        self.controller.insert("there.\nBye")
        self.wait(lambda: self.inserted)
        self.assertEqual(self.inserted, [("web", "there.\nBye")])
        self.assertEqual(self.run_js("t.value"), "Hello there.\nBye")
        self.assertEqual(self.tab.title(), "Typed")
        # Never into a password field: copied instead.
        self.run_js("document.getElementById('p').focus()")
        self.controller.target = ("web", self.tab)
        self.controller.insert("secret words")
        self.wait(lambda: len(self.inserted) == 2)
        self.assertEqual(self.inserted[1], ("copied", "secret words"))
        self.assertEqual(QApplication.clipboard().text(), "secret words")
        self.assertEqual(self.run_js("document.getElementById('p').value"), "")

    def test_words_go_into_rich_editors_and_shadow_fields(self):
        self.page(
            "<title>Form</title><div id=e contenteditable>Note:</div><div id=h></div>"
            "<script>const r = h.attachShadow({mode: 'open'});"
            "r.innerHTML = '<input id=i>';</script>"
        )
        self.run_js("e.focus(); getSelection().collapse(e.firstChild, 5); true")
        self.controller.target = ("web", self.tab)
        self.controller.insert("buy milk")
        self.wait(lambda: self.inserted)
        self.assertEqual(self.run_js("e.textContent"), "Note: buy milk")
        self.run_js("h.shadowRoot.getElementById('i').focus(); true")
        self.controller.target = ("web", self.tab)
        self.controller.insert("one\ntwo")
        self.wait(lambda: len(self.inserted) == 2)
        self.assertEqual(
            self.run_js("h.shadowRoot.getElementById('i').value"), "one two"
        )

    def test_steed_fields_take_the_words_and_nowhere_means_copied(self):
        field = QLineEdit("search")
        field.setCursorPosition(6)
        self.controller.target = ("field", field)
        self.controller.insert("for otters\nplease")
        self.assertEqual(field.text(), "search for otters please")
        self.controller.target = None
        self.controller.insert("loose words")
        self.assertEqual(QApplication.clipboard().text(), "loose words")
        self.assertEqual(self.inserted[-1], ("copied", "loose words"))

    def test_tap_and_hold_to_talk_and_escape_to_cancel(self):
        control = Qt.KeyboardModifier.ControlModifier
        both = control | Qt.KeyboardModifier.ShiftModifier
        space, press = Qt.Key.Key_Space, QEvent.Type.KeyPress
        release = QEvent.Type.KeyRelease
        with (
            patch.object(self.controller, "start", return_value=True) as start,
            patch.object(self.controller, "finish") as finish,
            patch.object(type(self.controller), "listening", False),
        ):
            # The shortcut becomes dictation's own key press.
            override = QKeyEvent(QEvent.Type.ShortcutOverride, space, both)
            QApplication.sendEvent(self.window.address_bar.address_input, override)
            self.assertTrue(override.isAccepted())
            self.key(press, space, both)
            self.assertEqual(start.call_count, 1)
            self.key(press, space, both, repeat=True)  # Held: no repeats.
            self.assertEqual(start.call_count, 1)
            self.key(release, space, Qt.KeyboardModifier.NoModifier)  # A tap.
            finish.assert_not_called()
            # Other keys are left alone.
            self.assertFalse(
                self.window._dictation_key(QKeyEvent(press, space, control))
            )
        with (
            patch.object(self.controller, "finish") as finish,
            patch.object(type(self.controller), "listening", True),
        ):
            self.key(press, space, both)  # Tapping again finishes.
            self.assertEqual(finish.call_count, 1)
        with (
            patch.object(self.controller, "start", return_value=True),
            patch.object(self.controller, "finish") as finish,
            patch.object(type(self.controller), "listening", False),
        ):
            self.key(press, space, both)
            self.controller.pressed_at -= 1  # Held for a second…
            with patch.object(type(self.controller), "listening", True):
                self.key(release, space, both)  # …and let go.
            self.assertEqual(finish.call_count, 1)
        with (
            patch.object(self.controller, "cancel") as cancel,
            patch.object(type(self.controller), "busy", True),
        ):
            self.key(press, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
            cancel.assert_called_once()

    def test_speaking_shows_live_words_then_writes_them_down(self):
        field = QLineEdit()
        self.controller._target = lambda: ("field", field)
        FakeRecorder.audio = spoken("draft so far")
        self.assertTrue(self.controller.start())
        self.assertTrue(self.controller.pill.isVisible())
        self.controller._live_decode()
        self.wait(lambda: self.controller.pill.text == "draft so far")
        self.assertEqual(field.text(), "")  # Live words only show in the pill.
        FakeRecorder.audio = spoken("Dear Ada, new line thanks")
        self.controller.finish()
        self.assertEqual(self.controller.pill.state, "working")
        self.wait(lambda: field.text())
        self.assertEqual(field.text(), "Dear Ada, thanks")  # One line: a field.
        self.assertFalse(self.controller.pill.isVisible())
        self.assertFalse(self.controller.busy)

    def test_a_too_short_press_and_a_failing_engine(self):
        field = QLineEdit()
        self.controller._target = lambda: ("field", field)
        FakeRecorder.audio = b"\0" * 100
        self.assertTrue(self.controller.start())
        self.controller.finish()
        self.assertFalse(self.controller.busy)
        self.controller.engine = None
        FakeRecorder.audio = spoken("words")
        self.assertTrue(self.controller.start())
        self.controller.engine.failed.emit("The dictation engine stopped.")
        self.assertFalse(self.controller.busy)
        self.assertIn("engine stopped", self.window.status_bar.currentMessage())

    def test_a_microphone_that_cannot_open_says_so(self):
        class NoMicrophone(FakeRecorder):
            def start(self):
                self.failed.emit("No microphone was found.")
                return False

        with patch.object(dictation, "Recorder", NoMicrophone):
            self.assertFalse(self.controller.start())
        self.assertFalse(self.controller.busy)
        self.assertEqual(
            self.window.status_bar.currentMessage(),
            "Dictation: No microphone was found.",
        )

    def test_missing_pieces_open_the_settings_instead(self):
        with patch.object(self.window, "open_dictation_settings") as opened:
            self.window.config.set("dictation_model", "parakeet-v3")
            self.assertFalse(self.controller.start())
            self.assertIn("Download a speech model", opened.call_args[0][0])
            with patch.object(dictation, "local_available", return_value=False):
                self.assertFalse(self.controller.start())
            self.assertIn("requirements-dictation.txt", opened.call_args[0][0])
        self.window.config.set("dictation_engine", "off")
        self.assertFalse(self.controller.start())
        self.assertIn("Dictation is off", self.window.status_bar.currentMessage())

    def test_private_windows_never_send_audio_away(self):
        self.window.config.set("dictation_engine", "online")
        with patch.object(
            self.window.profile_manager, "current_profile_name", "search"
        ):
            self.assertEqual(self.controller.ready()["engine"], "local")
        self.assertEqual(self.controller.ready()["engine"], "online")

    def test_settings_keep_the_key_in_the_keyring_only(self):
        from ui.dictation_dialog import DictationDialog

        kept = []
        with (
            patch.object(dictation, "api_key", return_value=""),
            patch.object(dictation, "set_api_key", side_effect=kept.append),
        ):
            dialog = DictationDialog(self.window.config, self.window)
            dialog.engine.setCurrentIndex(1)
            self.assertTrue(dialog.url.isVisibleTo(dialog))
            self.assertFalse(dialog.model.isVisibleTo(dialog))
            dialog.url.setText("http://example.com/v1/audio/transcriptions")
            dialog.key.setText("sk-synthetic-key")
            self.assertFalse(dialog.save())
            self.assertIn("https", dialog.status.text())
            dialog.url.setText("https://api.example.com/v1/audio/transcriptions")
            self.assertTrue(dialog.save())
        self.assertEqual(kept, ["sk-synthetic-key"])
        self.assertEqual(dialog.key.text(), "")
        self.assertEqual(self.window.config.get("dictation_engine"), "online")
        saved = (Path(self.directory.name) / ".custom_browser").rglob("*.json")
        for path in saved:
            self.assertNotIn("sk-synthetic-key", path.read_text(errors="ignore"))
        dialog.deleteLater()

    def test_sites_starting_speech_recognition_get_an_error_not_a_crash(self):
        ended = []
        self.tab.page().renderProcessTerminated.connect(
            lambda *args: ended.append(args)
        )
        self.page(
            "<title>Form</title><script>"
            "const r = new (window.SpeechRecognition || "
            "window.webkitSpeechRecognition)();"
            "r.onerror = e => document.title = 'error ' + e.error;"
            "r.onend = () => document.title += ' ended';"
            "r.start();</script>"
        )
        self.wait(lambda: self.tab.title() == "error service-not-allowed ended")
        self.assertEqual(self.run_js("1 + 1"), 2)
        self.assertEqual(ended, [])

    def test_the_menu_opens_dictation_settings(self):
        host = self.window.open_dictation_settings("Download a speech model first")
        self.assertIs(self.window.open_dictation_settings("again"), host)
        self.assertEqual(self.window._dictation_dialog.note.text(), "again")
        host.reject()


if __name__ == "__main__":
    unittest.main()
