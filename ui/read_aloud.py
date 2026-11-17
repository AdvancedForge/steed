"""Read Aloud (View → Read Aloud, Ctrl+Shift+U): the selection, or else the
page's article, spoken by the desktop's voices (speech-dispatcher), sentence
by sentence, with a small player at the bottom of the window: pause, slower,
faster, stop. Nothing leaves the computer.
"""

import re

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from browser import webengine

MAX_CHUNK = 400
RATE_STEP = 0.15
SELECTION = "(getSelection() ? getSelection().toString() : '')"
BODY = "(document.body ? document.body.innerText : '')"


def chunks(text):
    """Sentences (or parts of long ones), each short enough to stop between."""
    pieces = []
    for paragraph in re.split(r"\n\s*\n|\n", text or ""):
        paragraph = " ".join(paragraph.split())
        if not paragraph:
            continue
        for sentence in re.split(r"(?<=[.!?…])\s+", paragraph):
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


class ReadAloud(QObject):
    """Reading for one window; one text at a time."""

    def __init__(self, window, engine_name=None):
        super().__init__(window)
        self.window = window
        self.engine_name = engine_name
        self.engine = None
        self.queue, self.index = [], 0
        self.paused = False
        self.player = Player(self, window)

    @property
    def active(self):
        return bool(self.queue)

    def _engine(self):
        if self.engine is None:
            from PyQt6.QtTextToSpeech import QTextToSpeech

            names = QTextToSpeech.availableEngines()
            name = self.engine_name or ("speechd" if "speechd" in names else "")
            self.engine = QTextToSpeech(name, self) if name else QTextToSpeech(self)
            self.engine.stateChanged.connect(self._state)
            self.engine.setRate(float(self.window.config.get("read_aloud_rate", 0.0)))
        return self.engine

    def start(self, tab):
        """Read the selection in tab, else its article, else its text."""
        if tab is None or sip.isdeleted(tab):
            return
        page = tab.page()

        def selected(text):
            if isinstance(text, str) and text.strip():
                self.speak(text)
                return
            from ui.reader_view import EXTRACT, article

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

            webengine.run_script(page, EXTRACT, extracted, world=webengine.STEED_WORLD)

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
        self.player.show_chunk(text, self.index + 1, len(self.queue))
        self._engine().say(text)

    def _state(self, state):
        from PyQt6.QtTextToSpeech import QTextToSpeech

        if not self.queue:
            return
        if state == QTextToSpeech.State.Ready and not self.paused:
            self.index += 1
            self._say()
        elif state == QTextToSpeech.State.Error:
            self.window.status_bar.showMessage(
                "Read Aloud: the desktop's speech service did not answer "
                "(is speech-dispatcher installed?).",
                8000,
            )
            self.stop()

    def toggle(self):
        if not self.queue:
            return
        engine = self._engine()
        if self.paused:
            self.paused = False
            self.player.toggle.setText("Pause")
            engine.resume()
        else:
            self.paused = True
            self.player.toggle.setText("Play")
            engine.pause()

    def change_rate(self, step):
        rate = max(
            -1.0, min(1.0, float(self.window.config.get("read_aloud_rate", 0.0)) + step)
        )
        self.window.config.set("read_aloud_rate", round(rate, 2))
        self._engine().setRate(rate)

    def stop(self):
        queue, self.queue = self.queue, []
        self.paused = False
        self.player.toggle.setText("Pause")
        self.player.hide()
        if queue and self.engine is not None:
            self.engine.stop()
