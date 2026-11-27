"""Read Aloud (View → Read Aloud, Ctrl+Shift+U): the selection, or else the
page's article, spoken sentence by sentence, with a small player at the bottom
of the window: pause, slower, faster, voice, stop.

View → Read Aloud Voice… chooses who speaks: the desktop's voices
(speech-dispatcher, any of its voices; nothing leaves the computer) or your
own speech service (services.speech_service). Private windows always use the
desktop's voices.
"""

import re

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from browser import webengine

VOICES_FROM = ("system", "service")
MAX_CHUNK = 400
RATE_STEP = 0.15
SELECTION = "(getSelection() ? getSelection().toString() : '')"
BODY = "(document.body ? document.body.innerText : '')"


# Words ending in a full stop that don't end a sentence.
ABBREVIATIONS = frozenset(
    """dr mr mrs ms mx prof sr jr st mt ft vs e.g i.e u.s u.k u.n e.u
    no nos fig figs inc ltd co corp gov rep sen gen col capt lt sgt dept est
    approx apt ave blvd rd jan feb mar apr jun jul aug sep sept oct nov dec
    ph.d""".split()
)
# Said the way people say them; the rest is left to the voice.
SPOKEN = (
    (re.compile(r"\be\.g\.(?=\s|,|$)", re.I), "for example"),
    (re.compile(r"\bi\.e\.(?=\s|,|$)", re.I), "that is"),
    (re.compile(r"\bvs\.(?=\s)", re.I), "versus"),
    (re.compile(r"\betc\.(?=\s|$)", re.I), "et cetera"),
    # A link: just its site, not every character of it.
    (
        re.compile(r"https?://(?:www\.)?([^/\s]+)\S*", re.I),
        lambda m: f"a link to {m.group(1)}",
    ),
    # $2.3 billion -> 2.3 billion dollars (and euros, pounds).
    (
        re.compile(
            r"([$€£])\s?(\d[\d,]*(?:\.\d+)?)(\s?(?:million|billion|trillion|thousand|[mbk]n?)\b)?",
            re.I,
        ),
        lambda m: (
            f"{m.group(2)}{m.group(3) or ''} "
            + {"$": "dollars", "€": "euros", "£": "pounds"}[m.group(1)]
        ),
    ),
)


def spoken(text):
    for pattern, replacement in SPOKEN:
        text = pattern.sub(replacement, text)
    return text


def sentences(paragraph):
    """A paragraph's sentences, not broken after "Dr.", "e.g." or initials."""
    parts = re.split(r"(?<=[.!?…])\s+(?=[\"“'(\[]?[A-Z0-9À-ÖØ-Þ])", paragraph)
    found = []
    for part in parts:
        if found:
            last = found[-1].rstrip()
            word = last.split()[-1].lower().rstrip(".") if last.split() else ""
            initial = re.fullmatch(r"[a-z]", word)
            if word in ABBREVIATIONS or initial:
                found[-1] = last + " " + part
                continue
        found.append(part)
    return found


def chunks(text):
    """Sentences (or parts of long ones), each short enough to stop between."""
    pieces = []
    for paragraph in re.split(r"\n\s*\n|\n", text or ""):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        for sentence in sentences(paragraph):
            sentence = spoken(sentence)
            while len(sentence) > MAX_CHUNK:
                cut = sentence.rfind(" ", 0, MAX_CHUNK)
                cut = cut if cut > 0 else MAX_CHUNK
                pieces.append(sentence[:cut])
                sentence = sentence[cut:].strip()
            if sentence:
                pieces.append(sentence)
    return pieces


class Player(QWidget):
    """The bar at the bottom of the window while reading."""

    def __init__(self, reader, window):
        super().__init__(window)
        self.reader = reader
        self.setObjectName("ReadAloud")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 6, 10, 6)
        row.setSpacing(6)
        self.toggle = self._button("Pause", reader.toggle)
        self.slower = self._button("Slower", lambda: reader.change_rate(-RATE_STEP))
        self.faster = self._button("Faster", lambda: reader.change_rate(RATE_STEP))
        self.text = QLabel()
        self.text.setMinimumWidth(120)
        row.addWidget(self.toggle)
        row.addWidget(self.text, 1)
        row.addWidget(self.slower)
        row.addWidget(self.faster)
        row.addWidget(self._button("Voice", reader.choose_voice))
        row.addWidget(self._button("Stop", reader.stop))
        self.hide()

    def _button(self, text, action):
        button = QToolButton(self)
        button.setText(text)
        button.setAutoRaise(True)
        button.clicked.connect(action)
        return button

    def show_chunk(self, text, done, total):
        shown = self.text.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(80, self.text.width())
        )
        self.text.setText(shown)
        self.text.setToolTip(f"{done} of {total}")
        self.place()
        self.show()
        self.raise_()

    def place(self):
        owner = self.parentWidget()
        width = min(720, max(360, owner.width() - 80))
        self.setGeometry((owner.width() - width) // 2, owner.height() - 92, width, 48)
        colors = getattr(owner, "_shown_colors", None) or {}
        background = colors.get("toolbar", "palette(window)")
        ink = colors.get("ink", "palette(window-text)")
        hover = colors.get("hover", "palette(midlight)")
        self.setStyleSheet(
            f"#ReadAloud {{ background: {background}; border-radius: 24px;"
            f" border: 1px solid rgba(128,128,128,90); }}"
            f"#ReadAloud QLabel, #ReadAloud QToolButton {{ color: {ink}; }}"
            "#ReadAloud QLabel { background: transparent; border: none; }"
            "#ReadAloud QToolButton { background: transparent; border: none;"
            " border-radius: 14px; padding: 5px 10px; }"
            f"#ReadAloud QToolButton:hover {{ background: {hover}; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def system_engine(engine_name=None, parent=None):
    from PyQt6.QtTextToSpeech import QTextToSpeech

    names = QTextToSpeech.availableEngines()
    name = engine_name or ("speechd" if "speechd" in names else "")
    return QTextToSpeech(name, parent) if name else QTextToSpeech(parent)


class SystemVoice(QObject):
    """The desktop's voices through Qt (speech-dispatcher)."""

    finished = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, engine_name=None, rate=0.0, voice="", parent=None):
        super().__init__(parent)
        self.engine = system_engine(engine_name, self)
        self.engine.setRate(rate)
        for found in self.engine.availableVoices() if voice else ():
            if found.name() == voice:
                self.engine.setVoice(found)
                break
        self.engine.stateChanged.connect(self._state)

    def _state(self, state):
        from PyQt6.QtTextToSpeech import QTextToSpeech

        if state == QTextToSpeech.State.Ready:
            self.finished.emit()
        elif state == QTextToSpeech.State.Error:
            self.failed.emit(
                "Read Aloud: the desktop's speech service did not answer "
                "(is speech-dispatcher installed?)."
            )

    def say(self, text, upcoming=None):
        self.engine.say(text)

    def pause(self):
        self.engine.pause()

    def resume(self):
        self.engine.resume()

    def set_rate(self, rate):
        self.engine.setRate(rate)

    def stop(self):
        self.engine.stop()


def voice_from(window):
    """Who speaks in this window: "system" or "service"."""
    chosen = window.config.get("read_aloud_voice_from", "system")
    private = window.profile_manager.current_profile_name == "search"
    return chosen if chosen in VOICES_FROM and not private else "system"


class ReadAloud(QObject):
    """Reading for one window; one text at a time."""

    def __init__(self, window, engine_name=None):
        super().__init__(window)
        self.window = window
        self.engine_name = engine_name
        self.voice = None
        self.queue, self.index = [], 0
        self.paused = False
        self.player = Player(self, window)

    @property
    def active(self):
        return bool(self.queue)

    def _voice(self):
        if self.voice is None:
            config = self.window.config
            rate = float(config.get("read_aloud_rate", 0.0))
            if voice_from(self.window) == "service":
                from services.speech_service import ServiceVoice

                try:
                    self.voice = ServiceVoice(config, rate, self)
                except ValueError:
                    self.voice = None
            if self.voice is None:
                self.voice = SystemVoice(
                    self.engine_name,
                    rate,
                    str(config.get("read_aloud_system_voice", "") or ""),
                    self,
                )
            self.voice.finished.connect(self._spoken)
            self.voice.failed.connect(self._failed)
        return self.voice

    def voice_changed(self):
        """The voice settings changed: the next piece uses them."""
        self.stop()
        if self.voice is not None:
            self.voice.deleteLater()
            self.voice = None

    def choose_voice(self):
        self.window.open_read_aloud_settings()

    def start(self, tab):
        """Read the selection in tab, else its article, else its text."""
        if tab is None or sip.isdeleted(tab):
            return
        page = tab.page()

        def selected(text):
            if isinstance(text, str) and text.strip():
                self.speak(text)
                return
            from ui.reader_engines import extract_script
            from ui.reader_view import article

            def extracted(raw):
                found = article(raw)
                if found is not None:
                    self.speak(
                        "\n".join(
                            [found["title"]]
                            + [b["text"] for b in found["blocks"] if b["type"] != "img"]
                        )
                    )
                else:
                    webengine.run_script(
                        page, BODY, self.speak, world=webengine.STEED_WORLD
                    )

            webengine.run_script(
                page,
                extract_script(self.window.config),
                extracted,
                world=webengine.STEED_WORLD,
            )

        webengine.run_script(page, SELECTION, selected, world=webengine.STEED_WORLD)

    def speak(self, text):
        self.stop()
        self.queue = chunks(text if isinstance(text, str) else "")
        if not self.queue:
            self.window.status_bar.showMessage("There is no text to read here.", 4000)
            return
        self.index = 0
        self._say()

    def _say(self):
        if self.index >= len(self.queue):
            self.stop()
            return
        text = self.queue[self.index]
        upcoming = (
            self.queue[self.index + 1] if self.index + 1 < len(self.queue) else None
        )
        self.player.show_chunk(text, self.index + 1, len(self.queue))
        self._voice().say(text, upcoming)

    def _spoken(self):
        if self.queue and not self.paused:
            self.index += 1
            self._say()

    def _failed(self, message):
        if self.queue:
            self.window.status_bar.showMessage(message, 8000)
            self.stop()

    def toggle(self):
        if not self.queue:
            return
        voice = self._voice()
        if self.paused:
            self.paused = False
            self.player.toggle.setText("Pause")
            voice.resume()
        else:
            self.paused = True
            self.player.toggle.setText("Play")
            voice.pause()

    def change_rate(self, step):
        rate = max(
            -1.0, min(1.0, float(self.window.config.get("read_aloud_rate", 0.0)) + step)
        )
        self.window.config.set("read_aloud_rate", round(rate, 2))
        self._voice().set_rate(rate)

    def stop(self):
        queue, self.queue = self.queue, []
        self.paused = False
        self.player.toggle.setText("Pause")
        self.player.hide()
        if queue and self.voice is not None:
            self.voice.stop()
