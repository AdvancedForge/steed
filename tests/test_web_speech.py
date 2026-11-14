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

    def test_extension_pages_and_popups_are_answered(self):
        from test_extensions import write_extension

        from ui.extension_popup import ExtensionPopup

        folder = write_extension(Path(self.directory.name) / "talker")
        # Extension pages allow no inline scripts: the page's script is a file.
        script = PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
        (folder / "talk.js").write_text(script)
        (folder / "talk.html").write_text(
            "<title>Speech</title><script src=talk.js></script>"
        )
        name = self.window.profile_manager.current_profile_name
        extensions = self.window.profile_manager.registry.extensions
        extensions.install(name, folder)
        self.wait(lambda: extensions.installed(name))
        (entry,) = extensions.installed(name)
        url = f"chrome-extension://{entry['id']}/talk.html"
        self.tab.setUrl(QUrl(url))
        self.wait(lambda: self.tab.title() == "Speech")
        self.run_js("recognition.start(); 1")
        self.wait(lambda: self.speech.active)
        self.assertEqual(
            self.asked, [(f"chrome-extension://{entry['id']}", "MediaAudioCapture")]
        )
        # Asked by name, not by its id.
        self.assertEqual(
            self.window._extension_name(QUrl(f"chrome-extension://{entry['id']}")),
            "Steed Test Extension (extension)",
        )
        self.speech._level(0.5)
        self.pause()
        self.assertIn("result 0 final:hello world", self.wait_log("end"))
        # Its toolbar popup too, standing in for a tab.
        popup = ExtensionPopup(
            self.tab.page().profile(), url, "Talker", parent=self.window
        )
        try:
            popup.show()
            self.wait(lambda: popup.view.title() == "Speech")
            done = []
            popup.page.runJavaScript("recognition.start(); 1", done.append)
            self.wait(lambda: self.speech.active)
            self.assertIs(self.speech.tab, popup.page)
            self.speech._level(0.5)
            self.pause()

            def log():
                result = []
                popup.page.runJavaScript("log", result.append)
                self.wait(lambda: result)
                return result[0]

            self.wait(lambda: (log() or [None])[-1] == "end")
            self.assertIn("result 0 final:hello world", log())
            # Closing the popup while it listens ends the session.
            popup.page.runJavaScript("recognition.start(); 1")
            self.wait(lambda: self.speech.active)
        finally:
            popup.close()
        self.assertFalse(self.speech.active)

    def test_an_extensions_offscreen_document_is_steeds_and_is_answered(self):
        """Qt WebEngine's own offscreen documents end the browser: Steed opens
        them itself, one per extension, and answers their speech recognition."""
        import json

        from test_extensions import write_extension

        folder = write_extension(
            Path(self.directory.name) / "recorder",
            permissions=["offscreen", "storage"],
            background={"service_worker": "bg.js"},
        )
        (folder / "bg.js").write_text("""
chrome.runtime.onMessage.addListener((m, sender, reply) => {
  if (m.run) {
    (async () => {
      const out = [], make = () => chrome.offscreen.createDocument(
        {url: "off.html", reasons: ["USER_MEDIA"], justification: "listen"});
      await make(); out.push("made");
      try { await make(); out.push("twice"); } catch (e) { out.push("one only"); }
      out.push("has " + await chrome.offscreen.hasDocument());
      const found = await chrome.runtime.getContexts({contextTypes: ["OFFSCREEN_DOCUMENT"]});
      out.push("contexts " + found.length + " " + found[0].documentUrl.split("/").pop());
      reply(out.join(","));
    })();
    return true;
  }
  if (m.close) {
    chrome.offscreen.closeDocument().then(async () =>
      reply("closed, has " + await chrome.offscreen.hasDocument()));
    return true;
  }
  if (m.log) chrome.storage.local.set({log: m.log});
});
""")
        (folder / "off.html").write_text("<script src=off.js></script>")
        (folder / "off.js").write_text("""
const log = [], r = new webkitSpeechRecognition();
const send = () => chrome.runtime.sendMessage({log});
for (const t of ["start", "end"]) r.addEventListener(t, () => { log.push(t); send(); });
r.onresult = (e) => { log.push("final:" + e.results[0][0].transcript); send(); };
r.onerror = (e) => { log.push("error " + e.error); send(); };
r.start();
""")
        (folder / "probe.html").write_text(
            "<title>Speech</title><script src=probe.js></script>"
        )
        (folder / "probe.js").write_text(
            "window.ran = '';"
            "chrome.runtime.sendMessage({run: 1}, (r) => { window.ran = r; });"
        )
        name = self.window.profile_manager.current_profile_name
        extensions = self.window.profile_manager.registry.extensions
        extensions.install(name, folder)
        self.wait(lambda: extensions.installed(name))
        (entry,) = extensions.installed(name)
        self.tab.setUrl(QUrl(f"chrome-extension://{entry['id']}/probe.html"))
        self.wait(lambda: self.speech.active)
        self.assertEqual(
            self.asked, [(f"chrome-extension://{entry['id']}", "MediaAudioCapture")]
        )
        self.wait(lambda: self.run_js("window.ran"))
        self.assertEqual(
            self.run_js("window.ran"), "made,one only,has true,contexts 1 off.html"
        )
        self.speech._level(0.5)
        self.pause()

        def log():
            self.run_js(
                "chrome.storage.local.get('log', (v) => { window.got = v.log || []; }); 1"
            )
            return self.run_js("JSON.stringify(window.got || [])")

        self.wait(lambda: json.loads(log())[-1:] == ["end"])
        self.assertEqual(json.loads(log()), ["start", "final:hello world", "end"])
        # Closing it; it can be opened again (and listening ends with it).
        self.run_js(
            "chrome.runtime.sendMessage({close: 1}, (r) => { window.shut = r; }); 1"
        )
        self.wait(lambda: self.run_js("window.shut || ''"))
        self.assertEqual(self.run_js("window.shut"), "closed, has false")
        bridges = extensions.bridges
        self.assertIsNone(bridges.has_offscreen(name, entry["id"]))
        self.run_js(
            "window.ran = ''; chrome.runtime.sendMessage({run: 1}, (r) => { window.ran = r; }); 1"
        )
        self.wait(lambda: self.run_js("window.ran"))
        self.wait(lambda: self.speech.active)
        self.assertTrue(bridges.has_offscreen(name, entry["id"]))
        # Switching the extension off closes it.
        bridges.drop(name, entry["id"])
        self.assertIsNone(bridges.has_offscreen(name, entry["id"]))
        self.assertFalse(self.speech.active)

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
