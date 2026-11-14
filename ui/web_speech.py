"""A website's speech recognition session in one of this window's tabs.

The page's script (services.web_speech) asks; this records with the default
microphone after the site's microphone permission, transcribes with the
dictation engine, and answers with the standard events: start, audiostart,
soundstart/speechstart when you speak, interim and final results, speechend,
soundend, audioend and end. Without continuous mode it finishes after a pause;
with it, each pause gives a final result until the page stops.
"""

import json
import time
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl

from services import dictation, web_speech
from ui import dictation as window_dictation

VOICE_LEVEL = 0.04  # Peak level that counts as speaking.
PAUSE_SECONDS = 1.0  # A pause this long ends a phrase.
NO_SPEECH_SECONDS = 8.0
MAX_SECONDS = 60
TICK_MS = 150
MICROPHONE = SimpleNamespace(name="MediaAudioCapture")


# Secure pages answered: websites, and extension pages (their tabs and popups).
SECURE = ("https", "chrome-extension")


class WebSpeech(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.tab = None
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self._clear()

    def _clear(self):
        self.tab = None
        self.url = None
        self.session = None
        self.recorder = None
        self.engine = None
        self.chosen = {}
        self.key = ""
        self.options = {}
        self.finals = []  # Final phrases, in order (None while transcribing).
        self.words = []  # The final phrases the page has, without empty ones.
        self.waiting = {}  # Request id -> index in finals.
        self.partial_id = None
        self.segment_start = 0
        self.heard = False
        self.last_voice = 0.0
        self.started = 0.0
        self.last_partial = 0.0
        self.stopping = False

    @property
    def active(self):
        return self.session is not None

    # ----- Requests from pages -----

    def request(self, tab, text):
        message = web_speech.parse(text)
        if message is None:
            return
        # Answer after the console callback, outside the engine's call.
        QTimer.singleShot(0, lambda: self._handle(tab, message))

    def _handle(self, tab, message):
        if sip.isdeleted(tab) or self.window._closing:
            return
        if message["op"] == "start":
            self._start(tab, message)
        elif self.tab is tab and self.session == message["session"]:
            if message["op"] == "stop":
                self.stop()
            else:
                self._end(quietly=True)

    def _start(self, tab, message):
        session = message["session"]
        url = tab.url()
        local = url.host() in ("localhost", "127.0.0.1", "::1")
        if url.scheme() not in SECURE and not (url.scheme() == "http" and local):
            self._send(tab, url, session, "error", error="not-allowed")
            self._send(tab, url, session, "end")
            return
        user = window_dictation._listening
        if user and isinstance(user[0], window_dictation.WindowDictation):
            self._refuse(tab, url, session, "audio-capture", "Steed is dictating.")
            return
        if self.active:
            self._end(error="aborted")
        for other in list(user):
            if other is not self and not sip.isdeleted(other):
                other.cancel()
        chosen = self.window.dictation.ready(quiet=True)
        if chosen is None:
            self.window.status_bar.showMessage(
                f"{url.host()} wants to use speech recognition. "
                f"{self.window.dictation.problem} (General → Dictation)",
                10000,
            )
            self._refuse(
                tab, url, session, "service-not-allowed", self.window.dictation.problem
            )
            return
        origin = QUrl(url.adjusted(QUrl.UrlFormattingOption.RemovePath).toString())
        # The same question (and remembered answer) as the site's own
        # microphone requests.
        allowed = self.window._ask_permission(origin, MICROPHONE, tab.page())
        # The question may take a while: the page may have moved on.
        if sip.isdeleted(tab) or tab.url() != url or self.window._closing:
            return
        if not allowed:
            self._refuse(tab, url, session, "not-allowed", "Permission denied.")
            return
        self.tab, self.url, self.session = tab, url, session
        self.options = message
        self.chosen = chosen
        if chosen["engine"] == "local":
            self.engine = dictation.shared("local")
            kind = dictation.MODELS[chosen["model"]]["kind"]
            self.engine.ensure(chosen["path"], kind, chosen["language"])
        else:
            self.engine = dictation.shared("online")
            self.key = dictation.api_key()
        self.engine.result.connect(self._result)
        self.engine.failed.connect(self._failed)
        self.recorder = dictation.Recorder(self)
        problems = []
        self.recorder.failed.connect(problems.append)
        if not self.recorder.start():
            self._end(error="audio-capture")
            return
        self.recorder.failed.connect(lambda _: self._end(error="audio-capture"))
        self.recorder.level.connect(self._level)
        window_dictation._listening[:] = [self]
        self.started = self.last_voice = time.monotonic()
        self.timer.start()
        self._pill().show_state("listening", hint=self._hint())
        self.recorder.level.connect(self._pill().add_level)
        self._event("start")
        self._event("audiostart")

    def _refuse(self, tab, url, session, error, message=""):
        self._send(tab, url, session, "error", error=error, message=message)
        self._send(tab, url, session, "end")

    # ----- Listening -----

    def _level(self, level):
        if level < VOICE_LEVEL or self.stopping:
            return
        self.last_voice = time.monotonic()
        if not self.heard:
            self.heard = True
            self._event("soundstart")
            self._event("speechstart")

    def _tick(self):
        if not self.active:
            return
        if sip.isdeleted(self.tab) or self.tab.url() != self.url:
            self._end(quietly=True)  # The page moved on or closed.
            return
        now = time.monotonic()
        if self.stopping:
            return
        if now - self.started > MAX_SECONDS:
            self.stop()
        elif self.heard and now - self.last_voice > PAUSE_SECONDS:
            if self.options["continuous"]:
                self._phrase()
                self._event("speechend")
                self._event("soundend")
                self.heard = False
            else:
                self.stop()
        elif (
            not self.heard
            and now - self.last_voice > NO_SPEECH_SECONDS
            and not (self.finals)
        ):
            self.last_voice = now
            if not self.options["continuous"]:
                self._end(error="no-speech")
        elif (
            self.options["interim"]
            and self.heard
            and now - self.last_partial > window_dictation.LIVE_INTERVAL_MS / 1000
            and not self.engine.pending()
        ):
            self.last_partial = now
            audio = self.recorder.snapshot()[self.segment_start :]
            self.partial_id = next(window_dictation._ids)
            self._transcribe(self.partial_id, audio)

    def _phrase(self):
        """What was said since the last pause becomes a final result."""
        audio = self.recorder.snapshot()
        frame = self.recorder.channels * (4 if self.recorder.format == "float32" else 2)
        end = len(audio) - len(audio) % frame
        phrase, self.segment_start = audio[self.segment_start : end], end
        self.partial_id = None
        if len(phrase) < self.recorder.bytes_per_second() // 4:
            return
        request_id = next(window_dictation._ids)
        self.waiting[request_id] = len(self.finals)
        self.finals.append(None)
        self._transcribe(request_id, phrase)

    def _transcribe(self, request_id, audio):
        recorder = self.recorder
        if self.chosen["engine"] == "local":
            self.engine.transcribe(
                request_id, audio, recorder.rate, recorder.format, recorder.channels
            )
        else:
            self.engine.transcribe(
                request_id,
                audio,
                recorder.rate,
                recorder.format,
                recorder.channels,
                self.chosen["url"],
                self.chosen["online_model"],
                self.key,
                language=self.options["lang"].split("-")[0].lower()[:3],
            )

    def stop(self):
        """Stop listening; results still on their way are delivered, then end."""
        if not self.active or self.stopping:
            return
        self.stopping = True
        if self.heard or not self.finals:
            self._phrase()
        self.recorder.stop()
        if self.heard:
            self._event("speechend")
            self._event("soundend")
        self._event("audioend")
        self._pill().show_state("working")
        self._maybe_done()

    def abort(self):
        """Steed stops it (Escape, closing): the page hears "aborted"."""
        if self.active:
            self._end(error="aborted")

    cancel = abort  # Like the window's dictation, for whoever starts next.

    # ----- Results -----

    def _result(self, request_id, text):
        if not self.active:
            return
        text = text.strip()
        if request_id == self.partial_id:
            self.partial_id = None
            if text and not self.stopping:
                self._results(len(self.words), text)
                self._pill().show_state("listening", text, hint=self._hint())
            return
        if request_id not in self.waiting:
            return
        self.finals[self.waiting.pop(request_id)] = text
        # Deliver in order, leaving out phrases with no words.
        done = 0
        while done < len(self.finals) and self.finals[done] is not None:
            done += 1
        words = [value for value in self.finals[:done] if value]
        if len(words) > len(self.words):
            index, self.words = len(self.words), words
            self._results(index)
        elif self.stopping and not self.waiting and not self.words:
            self._event("nomatch")
        self._maybe_done()

    def _results(self, index, partial=""):
        items = [
            {"transcript": self._spaced(i, words), "final": True}
            for i, words in enumerate(self.words)
        ]
        if partial:
            items.append(
                {"transcript": self._spaced(len(self.words), partial), "final": False}
            )
        self._event("result", index=index, results=items)

    @staticmethod
    def _spaced(index, words):
        # Like Chrome: later phrases of a continuous session start with a space.
        return (" " + words) if index else words

    def _maybe_done(self):
        if self.stopping and not self.waiting:
            self._end()

    def _failed(self, error):
        if self.active:
            online = self.chosen.get("engine") == "online"
            self._end(error="network" if online else "audio-capture", message=error)

    def _end(self, error="", message="", quietly=False):
        tab, url, session = self.tab, self.url, self.session
        self.timer.stop()
        if self.recorder is not None and self.recorder.source is not None:
            self.recorder.stop()
        if self.engine is not None:
            for signal, slot in (
                (self.engine.result, self._result),
                (self.engine.failed, self._failed),
            ):
                try:
                    signal.disconnect(slot)
                except TypeError:
                    pass
        if window_dictation._listening and window_dictation._listening[0] is self:
            window_dictation._listening.clear()
        if self.window._dictation is not None and self.window._dictation.pill:
            if not self.window._dictation.busy:
                self.window._dictation.pill.hide()
        self._clear()
        if quietly or tab is None:
            return
        if error:
            self._send(tab, url, session, "error", error=error, message=message)
        self._send(tab, url, session, "end")

    # ----- Answers -----

    def _event(self, kind, **values):
        if self.active:
            self._send(self.tab, self.url, self.session, kind, **values)

    @staticmethod
    def _send(tab, url, session, kind, **values):
        from webengine import PAGE_WORLD, run_script

        if sip.isdeleted(tab) or tab.url() != url:
            return
        event = json.dumps({"type": kind, **values})
        run_script(
            tab.page(),
            f"window.__steedSpeechEvent && window.__steedSpeechEvent({session}, {event})",
            world=PAGE_WORLD,
        )

    def _pill(self):
        controller = self.window.dictation
        if controller.pill is None:
            controller.pill = window_dictation.ListeningPill(self.window)
        return controller.pill

    def _hint(self):
        return f"{self.url.host()} is listening…   Esc to stop"
