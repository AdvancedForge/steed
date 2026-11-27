"""Read Aloud's voices: the desktop's (any of them) or your own speech
service, which is asked for the next sentence while one plays."""

import io
import json
import threading
import unittest
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_read_aloud as reading
from PyQt6.QtCore import QTimer
from qt_test_support import WAIT_SECONDS, application

from services import speech_service
from services.speech_service import ServiceVoice
from ui.read_aloud import SystemVoice, voice_from


def silence(seconds=0.2):
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * int(16000 * seconds))
    return out.getvalue()


class Service(BaseHTTPRequestHandler):
    seen = []
    status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Service.seen.append((self.path, dict(self.headers), body))
        reply = silence()
        self.send_response(Service.status)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


def start_service(test):
    Service.seen.clear()
    Service.status = 200
    server = HTTPServer(("127.0.0.1", 0), Service)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return f"http://127.0.0.1:{server.server_port}/v1"


class Config(dict):
    def __init__(self, directory="", **values):
        super().__init__(values)
        self.config_dir = Path(directory)

    def set(self, key, value):
        self[key] = value


class QuietVoice(ServiceVoice):
    """Plays nothing: each piece "plays" at once."""

    played = []

    def _play(self, data):
        QuietVoice.played.append((self.current, data))
        QTimer.singleShot(0, lambda: self._status_end())

    def _status_end(self):
        if self.current:
            self.current = None
            self.finished.emit()


def wait(app, predicate, fail):
    import time

    end = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    fail("Timed out")


class ChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def window(self, chosen, profile="default"):
        return SimpleNamespace(
            config=Config(read_aloud_voice_from=chosen),
            profile_manager=SimpleNamespace(current_profile_name=profile),
        )

    def test_private_windows_always_use_the_desktop_voices(self):
        self.assertEqual(voice_from(self.window("service")), "service")
        self.assertEqual(voice_from(self.window("service", "search")), "system")
        self.assertEqual(voice_from(self.window("nonsense")), "system")

    def test_a_chosen_desktop_voice_is_used(self):
        voice = SystemVoice("mock", 0.2, "Anne")
        self.assertEqual(voice.engine.voice().name(), "Anne")
        self.assertAlmostEqual(voice.engine.rate(), 0.2, places=2)
        other = SystemVoice("mock", 0.0, "No such voice")
        self.assertNotEqual(other.engine.voice().name(), "No such voice")

    def test_rate_becomes_the_service_speed(self):
        self.assertEqual(
            [speech_service.speed(r) for r in (-1, 0, 0.5, 1, 3)],
            [0.5, 1.0, 1.41, 2.0, 2.0],
        )


class ServiceVoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = application()

    def setUp(self):
        self.url = start_service(self)
        QuietVoice.played.clear()
        self.config = Config(
            speech_service=self.url, speech_model="kokoro", speech_voice="af_heart"
        )
        self.events = []

    def voice(self, cls=QuietVoice, key=""):
        voice = cls(self.config, 0.5, key=key)
        voice.finished.connect(lambda: self.events.append("finished"))
        voice.failed.connect(self.events.append)
        self.addCleanup(voice.deleteLater)
        return voice

    def test_asks_for_the_next_piece_while_one_plays(self):
        voice = self.voice(key="sk-test")
        voice.say("First sentence.", "Second sentence.")
        wait(self.app, lambda: self.events and len(Service.seen) == 2, self.fail)
        self.assertEqual(self.events, ["finished"])
        path, headers, body = Service.seen[0]
        self.assertEqual(path, "/v1/audio/speech")
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(
            body,
            {
                "model": "kokoro",
                "input": "First sentence.",
                "voice": "af_heart",
                "response_format": "wav",
                "speed": 1.41,
            },
        )
        self.assertEqual(Service.seen[1][2]["input"], "Second sentence.")
        wait(self.app, lambda: "Second sentence." in voice.audio, self.fail)
        voice.say("Second sentence.")  # Already here: nothing more is asked.
        wait(self.app, lambda: len(self.events) == 2, self.fail)
        self.assertEqual(len(Service.seen), 2)
        self.assertEqual(
            [text for text, _ in QuietVoice.played],
            ["First sentence.", "Second sentence."],
        )
        self.assertTrue(QuietVoice.played[0][1].startswith(b"RIFF"))

    def test_no_voice_named_lets_the_service_choose(self):
        self.config["speech_voice"] = ""
        self.voice().say("Hello.")
        wait(self.app, lambda: self.events, self.fail)
        self.assertNotIn("voice", Service.seen[0][2])
        self.assertNotIn("Authorization", Service.seen[0][1])

    def test_failures_are_reported(self):
        Service.status = 401
        self.voice(key="wrong").say("Hello.")
        wait(self.app, lambda: self.events, self.fail)
        self.assertEqual(
            self.events, ["Read Aloud: the speech service refused the key."]
        )
        self.events.clear()
        Service.status = 500
        self.voice().say("Hello.")
        wait(self.app, lambda: self.events, self.fail)
        self.assertIn("did not answer (500)", self.events[0])

    def test_not_set_up(self):
        self.config["speech_model"] = ""
        self.voice().say("Hello.")
        self.assertIn("set up your speech service first", self.events[0])
        self.assertEqual(Service.seen, [])

    def test_stopping_drops_what_was_asked(self):
        voice = self.voice()
        voice.say("One.", "Two.")
        voice.stop()
        import time

        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(self.events, [])
        self.assertEqual((voice.audio, voice.fetching), ({}, {}))

    def test_real_playback_reaches_the_end(self):
        try:
            from PyQt6.QtMultimedia import QMediaDevices
        except ImportError:
            self.skipTest("No Qt Multimedia")
        if not QMediaDevices.audioOutputs():
            self.skipTest("No audio output here")
        voice = self.voice(ServiceVoice)
        voice.say("Quiet.")  # The service answers with silence.
        wait(self.app, lambda: self.events, self.fail)
        self.assertEqual(self.events, ["finished"])


class ReadingWithTheServiceTests(reading.ReadAloudTests):
    """The whole page, read by the service, in order."""

    def setUp(self):
        super().setUp()
        self.url = start_service(self)
        QuietVoice.played.clear()
        config = self.window.config
        config.set("speech_service", self.url)
        config.set("speech_model", "kokoro")
        config.set("read_aloud_voice_from", "service")
        self.reader.voice_changed()

    def test_reads_the_selection_then_stops(self):
        with (
            patch("services.speech_service.ServiceVoice", QuietVoice),
            patch("services.speech_service.api_key", return_value=""),
        ):
            self.reader.speak("Read just this. And this.")
            wait(self.app, lambda: not self.reader.active, self.fail)
        self.assertIsInstance(self.reader.voice, QuietVoice)
        self.assertEqual(
            [text for text, _ in QuietVoice.played], ["Read just this.", "And this."]
        )

    def test_reads_the_article_with_pause_speed_and_stop(self):
        pass  # Covered with the desktop's voices.


class DialogTests(unittest.TestCase):
    setUpClass = classmethod(reading.ReadAloudTests.setUpClass.__func__)
    setUp = reading.ReadAloudTests.setUp
    tearDown = reading.ReadAloudTests.tearDown

    def test_saving_the_service_and_back_to_the_desktop(self):
        from ui.read_aloud_dialog import ReadAloudVoiceDialog

        dialog = ReadAloudVoiceDialog(self.window, "mock")
        self.assertEqual(
            [dialog.system_voice.itemData(i) for i in range(3)], ["", "Anne", "Bob"]
        )
        self.assertTrue(dialog.service.isHidden())
        dialog.source.setCurrentIndex(1)
        self.assertFalse(dialog.service.isHidden())
        self.assertTrue(dialog.system_voice.isHidden())
        dialog.service.setText("http://speech.example/v1")
        self.assertFalse(dialog.save())
        self.assertIn("https", dialog.status.text())
        dialog.service.setText("http://localhost:8880/v1/")
        dialog.model.setText("kokoro")
        self.reader._voice()
        self.assertTrue(dialog.save())
        config = self.window.config
        self.assertEqual(config.get("speech_service"), "http://localhost:8880/v1")
        self.assertEqual(config.get("read_aloud_voice_from"), "service")
        self.assertIsNone(self.reader.voice)  # The next piece uses it.
        dialog.source.setCurrentIndex(0)
        dialog.system_voice.setCurrentIndex(1)
        self.assertTrue(dialog.save())
        self.assertEqual(config.get("read_aloud_system_voice"), "Anne")
        self.assertEqual(self.reader._voice().engine.voice().name(), "Anne")
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
