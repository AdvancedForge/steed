"""Push-to-talk dictation in a browser window, and its listening pill.

Ctrl+Shift+Space starts listening; a tap toggles, holding it talks until the
keys are let go. The words go wherever the cursor is: a field on the page, the
address bar or the agent sidebar's message. With nowhere to type, they are
copied so you can paste them.
"""

import itertools
import json
import time

from PyQt6 import sip
from PyQt6.QtCore import QObject, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QApplication,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from services import dictation

HOLD_SECONDS = 0.4
LIVE_INTERVAL_MS = 700
_ids = itertools.count(1)
_listening = []  # The one window controller listening now, if any.

INSERT = r"""
(function (text) {
  function deepest(doc) {
    let found = doc.activeElement;
    while (found) {
      if (found.shadowRoot && found.shadowRoot.activeElement) {
        found = found.shadowRoot.activeElement;
        continue;
      }
      if (found.tagName === "IFRAME" || found.tagName === "FRAME") {
        let inner = null;
        try { inner = found.contentDocument; } catch (error) {}
        if (inner && inner.activeElement && inner.activeElement !== inner.body) {
          found = inner.activeElement;
          continue;
        }
      }
      break;
    }
    return found;
  }
  function spaced(before, words) {
    if (!before || /\s$/.test(before) || /^[\s.,!?;:)\]]/.test(words)) return words;
    return " " + words;
  }
  const field = deepest(document);
  if (!field) return "none";
  const doc = field.ownerDocument;
  const typing = ["", "text", "search", "url", "email", "tel"];
  const kind = (field.getAttribute("type") || "").toLowerCase();
  if (field.tagName === "TEXTAREA" ||
      (field.tagName === "INPUT" && typing.includes(kind))) {
    if (field.readOnly || field.disabled) return "none";
    const start = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? start;
    let words = spaced(field.value.slice(0, start), text);
    if (field.tagName === "INPUT") words = words.replace(/\s*\n+\s*/g, " ");
    // insertText keeps the page's undo and its input events.
    if (!doc.execCommand("insertText", false, words)) {
      field.setRangeText(words, start, end, "end");
      field.dispatchEvent(new InputEvent("input", {
        bubbles: true, inputType: "insertText", data: words }));
    }
    return "inserted";
  }
  if (field.isContentEditable) {
    const selection = doc.getSelection();
    let before = "";
    if (selection && selection.rangeCount) {
      const range = selection.getRangeAt(0);
      if (range.startContainer.nodeType === Node.TEXT_NODE)
        before = range.startContainer.data.slice(0, range.startOffset);
    }
    const words = spaced(before, text);
    const lines = words.split("\n");
    lines.forEach(function (line, index) {
      if (index) doc.execCommand("insertLineBreak");
      if (line) doc.execCommand("insertText", false, line);
    });
    return "inserted";
  }
  return "none";
})(%s)
"""


class ListeningPill(QWidget):
    """A small rounded bar over the bottom of the window while you speak, in
    the window's own colours."""

    HINTS = {
        "listening": "Listening…   Ctrl+Shift+Space to finish · Esc to cancel",
        "working": "Writing it down…",
    }

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.levels = [0.0] * 9
        self.state = "listening"
        self.text = ""
        self.hint = None
        self.hide()

    def show_state(self, state, text="", hint=None):
        self.state, self.text, self.hint = state, text, hint
        self.place()
        self.show()
        self.raise_()
        self.update()

    def add_level(self, level):
        self.levels = self.levels[1:] + [level]
        self.update()

    def place(self):
        owner = self.parentWidget()
        width = min(620, max(300, owner.width() - 80))
        self.setGeometry((owner.width() - width) // 2, owner.height() - 84, width, 46)

    def paintEvent(self, event):
        # The browser's own toolbar colours, when it has them.
        colors = getattr(self.parentWidget(), "_shown_colors", None) or {}
        palette = self.parentWidget().palette()
        background = QColor(
            colors.get("toolbar") or palette.color(palette.ColorRole.Window)
        )
        text = QColor(colors.get("ink") or palette.color(palette.ColorRole.WindowText))
        accent = QColor(
            colors.get("accent") or palette.color(palette.ColorRole.Highlight)
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 22, 22)
        background.setAlpha(245)
        painter.fillPath(shape, background)
        edge = QColor(text)
        edge.setAlpha(50)
        painter.setPen(edge)
        painter.drawPath(shape)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        if self.state == "listening":
            for index, level in enumerate(self.levels):
                height = 4 + min(1.0, level * 3) * 20
                painter.drawRoundedRect(
                    QRectF(20 + index * 6, (46 - height) / 2, 3, height), 1.5, 1.5
                )
        else:
            phase = int(time.monotonic() * 6) % 3
            for index in range(3):
                painter.setOpacity(1.0 if index == phase else 0.35)
                painter.drawEllipse(QRectF(24 + index * 12, 19.5, 7, 7))
            painter.setOpacity(1.0)
        hint = self.hint if self.hint is not None else self.HINTS.get(self.state, "")
        words = self.text or hint
        muted = QColor(text)
        if not self.text:
            muted.setAlpha(170)
        painter.setPen(muted)
        font = painter.font()
        font.setPointSizeF(max(9.0, font.pointSizeF()))
        painter.setFont(font)
        box = QRectF(84, 0, self.width() - 104, self.height())
        # Long dictation shows its end, where the newest words are.
        shown = painter.fontMetrics().elidedText(
            words.replace("\n", " "), Qt.TextElideMode.ElideLeft, int(box.width())
        )
        painter.drawText(box, Qt.AlignmentFlag.AlignVCenter, shown)


class WindowDictation(QObject):
    """Dictation for one browser window."""

    inserted = pyqtSignal(str, str)  # where ("web", "field", "copied"), text

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.recorder = None
        self.pill = None
        self.engine = None
        self.final_id = None
        self.partial_ids = set()
        self.shown_partial = 0
        self.pressed_at = None
        self.target = None
        self.chosen = {}
        self.key = ""
        self.problem = ""
        self.live = QTimer(self)
        self.live.setInterval(LIVE_INTERVAL_MS)
        self.live.timeout.connect(self._live_decode)
        self.spinner = QTimer(self)
        self.spinner.setInterval(150)
        self.spinner.timeout.connect(lambda: self.pill and self.pill.update())

    # ----- State -----

    @property
    def listening(self):
        return self.recorder is not None and self.recorder.source is not None

    @property
    def busy(self):
        return self.listening or self.final_id is not None

    def message(self, text, timeout=6000):
        self.window.status_bar.showMessage(text, timeout)

    def ready(self, quiet=False):
        """The chosen engine can run now; otherwise say what is missing (in
        self.problem when quiet, for a website's request)."""
        self.problem = ""
        chosen = dictation.settings(self.window.config)
        if (
            self.window.profile_manager.is_search_profile()
            and chosen["engine"] == "online"
        ):
            # Private windows never send audio away.
            chosen["engine"] = "local"
        if chosen["engine"] == "off":
            self.problem = "Dictation is off. Turn it on in Dictation Settings."
            if not quiet:
                self.message(self.problem)
            return None
        if chosen["engine"] == "local":
            if not dictation.local_available():
                self.problem = "Set up dictation in Dictation Settings first."
                if quiet:
                    return None
                self.window.open_dictation_settings(
                    "Dictating on this computer needs one more package. Run "
                    ".venv/bin/python -m pip install -r requirements/dictation.txt "
                    "in Steed's folder, then start Steed again."
                )
                return None
            models = dictation.shared(
                "models", self.window.config.config_dir / "dictation-models"
            )
            if not models.installed(chosen["model"]):
                self.problem = "Download a speech model in Dictation Settings first."
                if quiet:
                    return None
                self.window.open_dictation_settings(
                    "Download a speech model first (once), then press "
                    "Ctrl+Shift+Space again."
                )
                return None
            chosen["path"] = models.path(chosen["model"])
        return chosen

    # ----- Push to talk -----

    def key_pressed(self, repeat):
        if repeat:
            return
        if self.listening:
            self.finish()
        elif self.final_id is None:
            self.pressed_at = time.monotonic()
            if not self.start():
                self.pressed_at = None

    def key_released(self, repeat):
        if repeat or self.pressed_at is None:
            return
        held = time.monotonic() - self.pressed_at
        self.pressed_at = None
        if held >= HOLD_SECONDS and self.listening:
            self.finish()

    def toggle(self):
        if self.listening:
            self.finish()
        elif self.final_id is None:
            self.start()

    def start(self):
        chosen = self.ready()
        if chosen is None:
            return False
        for other in list(_listening):
            if other is not self and not sip.isdeleted(other):
                other.cancel()
        self.chosen = chosen
        self.target = self._target()
        if chosen["engine"] == "local":
            self.engine = dictation.shared("local")
            self.engine.ensure(
                chosen["path"],
                dictation.MODELS[chosen["model"]]["kind"],
                chosen["language"],
            )
        else:
            self.engine = dictation.shared("online")
            self.key = dictation.api_key()
        self._connect(True)
        self.recorder = dictation.Recorder(self)
        if self.pill is None:
            self.pill = ListeningPill(self.window)
        self.recorder.level.connect(self.pill.add_level)
        problems = []
        self.recorder.failed.connect(problems.append)
        if not self.recorder.start():
            self._connect(False)
            self.recorder = None
            self.message(
                f"Dictation: {problems[0] if problems else 'no microphone.'}", 9000
            )
            return False
        self.recorder.failed.connect(self._failed)
        _listening[:] = [self]
        self.shown_partial = 0
        self.pill.show_state("listening")
        if chosen["engine"] == "local" and chosen["live"]:
            self.live.start()
        return True

    def finish(self):
        """Stop listening and write down what was said."""
        if not self.listening:
            return
        self.live.stop()
        audio = self.recorder.stop()
        if _listening and _listening[0] is self:
            _listening.clear()
        if len(audio) < self.recorder.bytes_per_second() // 4:
            self._reset()
            if self.target and self.target[0] == "test":
                self.target[1].heard("")
            return
        self.final_id = next(_ids)
        self.pill.show_state("working")
        self.spinner.start()
        self._send(self.final_id, audio)

    def cancel(self):
        self.live.stop()
        if self.recorder is not None:
            self.recorder.stop()
        if _listening and _listening[0] is self:
            _listening.clear()
        self._reset()

    def _reset(self):
        self._connect(False)
        self.final_id = None
        self.partial_ids.clear()
        self.recorder = None
        self.spinner.stop()
        if self.pill is not None:
            self.pill.hide()

    def _connect(self, on):
        if self.engine is None:
            return
        try:
            if on:
                self.engine.result.connect(self._result)
                self.engine.failed.connect(self._failed)
            else:
                self.engine.result.disconnect(self._result)
                self.engine.failed.disconnect(self._failed)
        except TypeError:
            pass
        if not on:
            self.engine = None

    def _send(self, request_id, audio):
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
            )

    def _live_decode(self):
        # One partial at a time, so a slow computer never falls behind.
        if not self.listening or self.engine is None or self.engine.pending():
            return
        audio = self.recorder.snapshot()
        if len(audio) < self.recorder.bytes_per_second() // 2:
            return
        request_id = next(_ids)
        self.partial_ids.add(request_id)
        self._send(request_id, audio)

    def _result(self, request_id, text):
        if request_id in self.partial_ids:
            self.partial_ids.discard(request_id)
            if self.listening and request_id > self.shown_partial and text:
                self.shown_partial = request_id
                self.pill.show_state("listening", text)
            return
        if request_id != self.final_id:
            return
        self._reset()
        words = dictation.tidy(text)
        if self.target and self.target[0] == "test":
            # Dictation Settings' microphone test shows what was heard.
            test, self.target = self.target[1], None
            if not sip.isdeleted(test):
                test.heard(words)
        elif words:
            self.insert(words)
        else:
            self.message(
                "Nothing was heard. Check the microphone in Dictation Settings."
            )

    def _failed(self, error):
        if not self.busy:
            return
        if (
            self.target
            and self.target[0] == "test"
            and not sip.isdeleted(self.target[1])
        ):
            self.target[1].heard(None, error)
        self.cancel()
        self.message(f"Dictation: {error}", 9000)

    # ----- Where the words go -----

    def _target(self):
        """What has the cursor now: a Steed field or a web view (a page, the
        agent sidebar…), in this window."""
        from browser.webengine import view_around

        focused = QApplication.focusWidget()
        if focused is None or focused.window() is not self.window:
            return ("copy", None)
        if isinstance(focused, (QLineEdit, QPlainTextEdit, QTextEdit)):
            return ("field", focused)
        view = view_around(focused)
        return ("web", view) if view is not None else ("copy", None)

    def insert(self, words):
        kind, target = self.target or ("copy", None)
        self.target = None
        if target is None or sip.isdeleted(target):
            self._copy(words)
        elif kind == "field" and not target.isReadOnly():
            if isinstance(target, QLineEdit):
                before = target.text()[: target.cursorPosition()]
                words = words.replace("\n", " ")
            else:
                cursor = target.textCursor()
                before = cursor.block().text()[: cursor.positionInBlock()]
            if before and not before[-1].isspace() and words[:1] not in ".,!?;:":
                words = " " + words
            if isinstance(target, QLineEdit):
                target.insert(words)
            else:
                target.insertPlainText(words)
            self.inserted.emit("field", words)
        elif kind == "web":
            from browser.webengine import STEED_WORLD, run_script

            def done(result):
                if result == "inserted":
                    self.inserted.emit("web", words)
                else:
                    self._copy(words)

            run_script(
                target.page(), INSERT % json.dumps(words), done, world=STEED_WORLD
            )
        else:
            self._copy(words)

    def _copy(self, words):
        QApplication.clipboard().setText(words)
        self.message("Dictation copied — paste it with Ctrl+V.")
        self.inserted.emit("copied", words)
