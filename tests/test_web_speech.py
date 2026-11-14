"""Websites' SpeechRecognition answered by Steed's dictation: permission,
events in the standard order, interim and continuous results, and stopping."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, Qt, QUrl
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication
from qt_test_support import WAIT_SECONDS, application, keep_directory
from test_dictation import FAKE_WORKER, FakeRecorder, spoken

from browser_window import BrowserWindow
from services import dictation, web_speech

PAGE = """<title>Speech</title><script>
window.log = [];
window.recognition = new webkitSpeechRecognition();
for (const type of ["start", "audiostart", "soundstart", "speechstart",
    "speechend", "soundend", "audioend", "nomatch", "end"]) {
  recognition.addEventListener(type, () => log.push(type));
}
recognition.onresult = (event) => log.push("result " + event.resultIndex + " " +
  Array.from(event.results, (r) => (r.isFinal ? "final:" : "interim:") +
    r[0].transcript).join("|"));
recognition.onerror = (event) => log.push("error " + event.error);
</script>"""


class WebSpeechTests(unittest.TestCase):
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
        models = Path(self.directory.name) / ".custom_browser" / "dictation-models"
        model = models / dictation.MODELS[dictation.DEFAULT_MODEL]["archive"]
        model.mkdir(parents=True)
        (model / "x").touch()
        self.tab = self.window.tabs[self.window.current_tab_index]
        self.speech = self.window.web_speech
        self.asked = []
        self.answer = True
        self.permission = patch.object(
            self.window, "_ask_permission", side_effect=self.ask
        )
        self.permission.start()
        FakeRecorder.audio = spoken("hello world")

    def ask(self, origin, feature, page=None):
        self.asked.append((origin.toString(), feature.name))
        return self.answer

    def tearDown(self):
        self.window.close()
        self.permission.stop()
        dictation.stop_shared()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.worker.stop()
        self.recorder.stop()
        self.home.stop()

    def wait(self, predicate):
        end = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out")

    def run_js(self, script):
        result = []
        self.tab.page().runJavaScript(script, result.append)
        self.wait(lambda: result)
        return result[0]

    def load(self, url="https://example.org/talk", html=PAGE):
        self.tab.setHtml(html, QUrl(url))
        self.wait(lambda: self.tab.title() == "Speech")

    def log(self):
        return self.run_js("log")

    def wait_log(self, last):
        self.wait(lambda: (self.log() or [None])[-1] == last)
        return self.log()

    def pause(self):
        """The speaker stops for longer than a pause."""
        self.speech.last_voice -= 5
        self.speech._tick()

    def test_a_phrase_with_interim_results_in_the_standard_order(self):
        self.load()
        self.run_js("recognition.interimResults = true; recognition.start(); 1")
        self.wait(lambda: self.speech.active)
        self.assertEqual(self.asked, [("https://example.org", "MediaAudioCapture")])
        self.speech._level(0.01)  # Background noise is not speech.
        self.assertFalse(self.speech.heard)
        self.speech._level(0.5)
        FakeRecorder.audio = spoken("hello")
        self.speech._tick()
        self.wait_log("result 0 interim:hello")
        FakeRecorder.audio = spoken("hello world")
        self.pause()
        self.assertEqual(
            self.wait_log("end"),
            [
                "start",
                "audiostart",
                "soundstart",
                "speechstart",
                "result 0 interim:hello",
                "speechend",
                "soundend",
                "audioend",
                "result 0 final:hello world",
                "end",
            ],
        )
        self.assertFalse(self.speech.active)
        self.assertFalse(self.window.dictation.pill.isVisible())
        # It can start again.
        self.run_js("log.length = 0; recognition.start(); 1")
        self.wait(lambda: self.speech.active)
        self.run_js("recognition.stop(); 1")
        self.assertIn("audioend", self.wait_log("end"))

    def test_continuous_sessions_give_a_final_result_per_phrase(self):
        self.load()
        self.run_js("recognition.continuous = true; recognition.start(); 1")
        self.wait(lambda: self.speech.active)
        self.speech._level(0.5)
        FakeRecorder.audio = spoken("first part", 1)
        self.pause()
        self.wait_log("result 0 final:first part")
        self.assertTrue(self.speech.active)  # Still listening.
        self.speech._level(0.5)
        FakeRecorder.audio = spoken("first part", 1) + spoken("then more", 1)
        self.pause()
        self.wait_log("result 1 final:first part|final: then more")
        self.run_js("recognition.stop(); 1")
        log = self.wait_log("end")
        self.assertEqual(log.count("speechstart"), 2)
        self.assertEqual(log[-2:], ["audioend", "end"])

    def test_refused_permission_and_dictation_that_is_not_set_up(self):
        self.answer = False
        self.load()
        self.run_js("recognition.start(); 1")
        self.assertEqual(self.wait_log("end"), ["error not-allowed", "end"])
        self.answer = True
        self.window.config.set("dictation_engine", "off")
        self.run_js("log.length = 0; recognition.start(); 1")
        self.assertEqual(self.wait_log("end"), ["error service-not-allowed", "end"])
        self.assertIn(
            "example.org wants to use speech recognition",
            self.window.status_bar.currentMessage(),
        )
        self.assertEqual(len(self.asked), 1)  # Not asked when it cannot work.

    def test_escape_aborts_and_leaving_the_page_ends_it(self):
        self.load()
        self.run_js("recognition.start(); 1")
        self.wait(lambda: self.speech.active)
        QApplication.sendEvent(
            self.window.address_bar.address_input,
            QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
            ),
        )
        self.assertEqual(self.wait_log("end")[-2:], ["error aborted", "end"])
        self.run_js("recognition.start(); 1")
        self.wait(lambda: self.speech.active)
        self.tab.setHtml("<title>Elsewhere</title>", QUrl("https://example.org/b"))
        self.wait(lambda: not self.speech.active)

    def test_only_secure_top_pages_are_answered(self):
        self.load("http://example.org/plain")
        self.run_js("recognition.start(); 1")
        self.assertEqual(self.wait_log("end"), ["error service-not-allowed", "end"])
        self.load(
            html="<title>Speech</title><iframe srcdoc='<script>"
            'const r = new SpeechRecognition(); r.onerror = e => parent.document.title = "frame " + e.error;'
            "r.start()</script>'></iframe>"
        )
        self.wait(lambda: self.tab.title() == "frame service-not-allowed")
        self.assertEqual(self.asked, [])

    def test_steed_dictation_in_progress_and_forged_requests(self):
        self.load()
        field_target = ("copy", None)
        with patch.object(self.window.dictation, "_target", return_value=field_target):
            self.assertTrue(self.window.dictation.start())
        self.run_js("recognition.start(); 1")
        self.assertEqual(self.wait_log("end"), ["error audio-capture", "end"])
        self.window.dictation.cancel()
        self.assertIsNone(web_speech.parse(web_speech.PREFIX + '{"token": "guess"}'))
        self.assertIsNone(web_speech.parse(web_speech.PREFIX + "not json"))

    def test_starting_twice_is_an_error_like_chrome(self):
        self.load()
        self.assertEqual(
            self.run_js(
                "recognition.start(); try { recognition.start(); 'no' }"
                " catch (e) { e.name }"
            ),
            "InvalidStateError",
        )


if __name__ == "__main__":
    unittest.main()
